#
# This source file is part of the Bramble open source project.
#
# Copyright (c) 2026 Jaldis B.V.
#
# Licensed under the MIT OR Apache-2.0 license (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://opensource.org/licenses/MIT
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

from __future__ import annotations

import asyncio
import datetime
import decimal
import inspect
import uuid
from collections.abc import AsyncGenerator, Callable, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from bramble._bramble import lower_query, validate_query
from bramble._dependency import DependencyScope, resolve_dependencies
from bramble._error import ErrorCode, GraphQLError
from bramble._error import error_to_dict as _error_to_dict
from bramble._interface import resolve_interface_type
from bramble._resolver import Info
from bramble._union import resolve_union_type
from bramble.directive import apply_directive

if TYPE_CHECKING:
    from bramble._bramble import ArgumentInfo, FieldInfo, GraphQLTypeInfo, LoweredField
    from bramble._schema import Schema


@dataclass(frozen=True, slots=True)
class Path:
    """One segment of a GraphQL response path (§8's `path` field), linked back to its parent --
    mirrors graphql-core's own `Path` rather than a plain list, so building one for a deeply
    nested field is O(1) (append a segment) instead of O(depth) (copy-and-append a list).
    """

    key: str | int
    prev: "Path | None" = None

    def as_list(self) -> list[str | int]:
        segments: list[str | int] = []
        node: Path | None = self
        while node is not None:
            segments.append(node.key)
            node = node.prev
        segments.reverse()
        return segments


@dataclass(frozen=True, slots=True)
class SelectedField:
    """A read-only view of one of the current field's own sub-selections (`Info.selected_fields`),
    for a resolver that wants to inspect what's being asked of it (e.g. to avoid fetching a column
    nothing selected). Only one level deep -- each entry's own `selections` goes one level further,
    same as the query itself nests.
    """

    name: str
    arguments: dict[str, Any]
    selections: list["SelectedField"]


def _selected_fields(lowered_fields: Sequence["LoweredField"]) -> list[SelectedField]:
    return [
        SelectedField(
            name=lowered.field_name,
            arguments=dict(lowered.arguments),
            selections=_selected_fields(lowered.selections),
        )
        for lowered in lowered_fields
    ]


class _PropagateNull(Exception):
    """Internal control-flow signal (§8/§11): a field's value could not be completed and its
    declared type is non-null, so the failure must keep bubbling up past this slot to the nearest
    ancestor whose type allows null (per spec: "this error then propagates to be handled by the
    parent field"). Never raised across a public function boundary -- always caught somewhere
    within `_execute_operation`'s own recursion, since the outermost call always catches it too.
    """


@dataclass
class _ExecutionState:
    """Values that stay constant for one whole `execute_async` call, threaded through the
    recursive executor instead of repeated as five separate parameters everywhere.
    """

    schema: "Schema"
    context: Any
    root_value: Any
    variable_values: dict[str, Any]
    query: str | None
    errors: list[GraphQLError]
    dependency_scope: DependencyScope


def _build_info(
    *,
    field_name: str,
    python_name: str,
    path: Path,
    selections: Sequence["LoweredField"],
    state: _ExecutionState,
) -> Info:
    """`field_name` is the name as written in the query (camelCase by default); `python_name` is
    the resolver/attribute identifier it maps back to (`postId` -> `post_id`). They are genuinely
    different values whenever `auto_camel_case` or an explicit `name=` override is in play, so a
    caller must supply both -- passing the query name for each is what made `info.python_name`
    report camelCase.
    """
    info = Info()
    info.field_name = field_name
    info.python_name = python_name
    info.context = state.context
    info.root_value = state.root_value
    info.variable_values = state.variable_values
    info.query = state.query
    info.path = path
    info.selected_fields = _selected_fields(selections)
    info.schema = state.schema
    return info


def _to_camel_case(name: str) -> str:
    """Mirrors `bramble_core::naming::to_camel_case` exactly (`post_id` -> `postId`) -- must stay
    in lockstep with the Rust implementation validation uses, or a query could pass validation
    (Rust) but fail to bind at execution time (Python), or vice versa.
    """
    result: list[str] = []
    capitalize_next = False
    for char in name:
        if char == "_":
            capitalize_next = True
        elif capitalize_next:
            result.append(char.upper())
            capitalize_next = False
        else:
            result.append(char)
    return "".join(result)


def _effective_name(name: str, graphql_name: str | None, *, auto_camel_case: bool) -> str:
    if graphql_name is not None:
        return graphql_name
    return _to_camel_case(name) if auto_camel_case else name


def _map_arguments(
    argument_defs: Sequence["ArgumentInfo"], provided: dict[str, Any], *, auto_camel_case: bool
) -> dict[str, Any]:
    """Maps a dict keyed by GraphQL argument name (as written in the query, already resolved to
    real Python values by `lower_query`) onto the Python keyword names a resolver/directive
    function actually declares. Mirrors `argument_key()`'s convention from `bramble-core`'s own
    validation, just evaluated here in Python: which concrete type ends up owning a field (and
    thus its true parameter names) isn't known until execution reaches it, so this mapping can't
    be precomputed during lowering (see `LoweredField`'s own doc comment).
    """
    kwargs: dict[str, Any] = {}
    for argument in argument_defs:
        graphql_key = _effective_name(argument.name, argument.graphql_name, auto_camel_case=auto_camel_case)
        if graphql_key in provided:
            kwargs[argument.name] = provided[graphql_key]
    return kwargs


def _enum_graphql_name(enum_class: type, member: Any) -> str:
    """The GraphQL name a resolved enum member is sent as -- its `bramble.enum_value(name=...)`
    override if it declared one, else the Python member's own identifier.
    """
    for value_info in enum_class.__bramble_type_info__.enum_values:
        if value_info.name == member.name:
            return value_info.graphql_name or value_info.name
    # Not a member of this enum at all -- a resolver returned something else entirely. Reported as
    # a field error rather than silently emitting a name no client could match.
    raise GraphQLError(
        f"'{member!r}' is not a member of enum '{enum_class.__bramble_type_info__.name}'",
        code=ErrorCode.FIELD_RESOLUTION_FAILED,
    )


def _enum_member_from_graphql_name(enum_class: type, name: Any) -> Any:
    """The Python enum member an incoming GraphQL enum value names -- the inverse of
    `_enum_graphql_name`, applied to arguments/variables before a resolver ever sees them, so a
    resolver receives `Color.RED` rather than the bare string `"RED"`.
    """
    for value_info in enum_class.__bramble_type_info__.enum_values:
        if (value_info.graphql_name or value_info.name) == name:
            return enum_class[value_info.name]
    raise GraphQLError(
        f"'{name}' is not a valid value for enum '{enum_class.__bramble_type_info__.name}'",
        code=ErrorCode.ARGUMENT_TYPE_MISMATCH,
    )


def _coerce_value(type_info: "GraphQLTypeInfo", value: Any, schema: "Schema") -> Any:
    """Coerces a resolved argument value to what a resolver should actually receive, recursing
    through the value's own declared type structure: `NonNull`/`List` wrapping (each list item
    coerced individually); a registered custom scalar's `parse_value` hook (§3b); and an input
    object's own fields (each coerced by *that field's* declared type, matched by the same
    graphql_name-or-camelCase convention field/argument lookup uses everywhere else -- an input
    type is schema-registered the same way an object type is, just with `kind == "input"`), with
    the coerced dict then instantiated as a real instance of the `@bramble.input`-decorated class
    -- `graphql_value_to_python` only ever produces a plain dict for an input object literal, so
    without this step a resolver typed `Parent[SomeInput]`/`filter: SomeInput` would always
    receive a bare dict instead, keyed by GraphQL name rather than Python attribute name.
    """
    if value is None:
        return None

    if type_info.kind == "NON_NULL":
        return _coerce_value(type_info.of_type, value, schema)

    if type_info.kind == "LIST":
        return [_coerce_value(type_info.of_type, item, schema) for item in value]

    type_name = type_info.name
    assert type_name is not None  # NAMED is the only remaining kind, and it always carries a name.

    scalar_definition = schema.scalars_by_name.get(type_name)
    if scalar_definition is not None and scalar_definition.parse_value is not None:
        return scalar_definition.parse_value(value)

    named_type = schema.types_by_name.get(type_name)
    if named_type is not None and named_type.__bramble_type_info__.kind == "enum":
        # An enum literal reaches here as the plain name string `lower_query` produced -- turn it
        # into the real Python member. Recursion through `NON_NULL`/`LIST`/input-object fields
        # above means this covers a list of enums and an enum nested in an input object too, the
        # same way custom scalar `parse_value` coercion already does.
        return _enum_member_from_graphql_name(named_type, value)

    input_type = named_type
    if input_type is not None and input_type.__bramble_type_info__.kind == "input" and isinstance(value, dict):
        fields_by_key = {
            _effective_name(field_info.name, field_info.graphql_name, auto_camel_case=schema.config.auto_camel_case): (
                field_info
            )
            for field_info in input_type.__bramble_type_info__.fields
        }
        kwargs = {}
        for key, item_value in value.items():
            field_info = fields_by_key.get(key)
            if field_info is None:
                # Shouldn't happen post-validation (an unknown input field is a validation-time
                # error) -- pass through defensively rather than silently dropping the key.
                kwargs[key] = item_value
            else:
                kwargs[field_info.name] = _coerce_value(field_info.type_info, item_value, schema)
        return input_type(**kwargs)

    return value


async def _bind_resolver_kwargs(
    field_info: "FieldInfo",
    lowered_field: "LoweredField",
    parent_value: Any,
    info: Info,
    schema: "Schema",
    resolver: Callable[..., Any],
    concrete_type: type,
    scope: DependencyScope,
) -> dict[str, Any]:
    kwargs = _map_arguments(field_info.arguments, lowered_field.arguments, auto_camel_case=schema.config.auto_camel_case)
    for argument in field_info.arguments:
        if argument.name in kwargs:
            kwargs[argument.name] = _coerce_value(argument.type_info, kwargs[argument.name], schema)
    if field_info.parent_parameter is not None:
        kwargs[field_info.parent_parameter] = parent_value
    if field_info.info_parameter is not None:
        kwargs[field_info.info_parameter] = info
    kwargs.update(await resolve_dependencies(resolver, cls=concrete_type, info=info, scope=scope))
    return kwargs


async def _resolve_field_value(
    field_info: "FieldInfo",
    lowered_field: "LoweredField",
    parent_value: Any,
    info: Info,
    concrete_type: type,
    schema: "Schema",
    scope: DependencyScope,
) -> Any:
    if not field_info.has_resolver:
        return getattr(parent_value, field_info.name)

    resolver = getattr(concrete_type, field_info.name)
    kwargs = await _bind_resolver_kwargs(field_info, lowered_field, parent_value, info, schema, resolver, concrete_type, scope)
    result = resolver(**kwargs)
    if inspect.isawaitable(result):
        result = await result
    return result


async def _apply_custom_directives(
    directives: Sequence[Any], value: Any, schema: "Schema", *, info: Info, scope: DependencyScope
) -> Any:
    """Applies a field's custom operation directives in order (§7), each receiving the previous
    one's output -- `@skip`/`@include` never appear here, since `lower_query` already applied them
    structurally rather than carrying them through to execution. `info`/`scope` support a directive
    function's own `Info`/`Depends[T]` parameters (§3c) -- injectable in a custom operation
    directive the same way they are in a resolver.
    """
    for directive in directives:
        directive_function = schema.directive_functions_by_name.get(directive.name)
        if directive_function is None:
            raise GraphQLError(
                f"unknown operation directive '@{directive.name}'",
                code=ErrorCode.INVALID_DIRECTIVE_LOCATION,
            )
        directive_info = directive_function.__bramble_directive_info__
        mapped_arguments = _map_arguments(
            directive_info.arguments, directive.arguments, auto_camel_case=schema.config.auto_camel_case
        )
        for argument in directive_info.arguments:
            if argument.name in mapped_arguments:
                mapped_arguments[argument.name] = _coerce_value(argument.type_info, mapped_arguments[argument.name], schema)
        value = await apply_directive(directive_function, value, mapped_arguments, info=info, scope=scope)
    return value


def _serialize_scalar(type_name: str, value: Any, schema: "Schema") -> Any:
    """Serializes a resolved leaf value for the response (§3b): a registered custom scalar's own
    `serialize` hook takes priority (also how built-in scalar behavior gets overridden); otherwise
    the standard-library date/time/decimal/UUID types get their spec-mandated conversion, and
    everything else (str/int/float/bool/None, or a value already dict/list-shaped) passes through
    as-is -- `graphql_value_to_python`/plain Python values already match what JSON encoding wants.
    """
    if value is None:
        return None
    scalar_definition = schema.scalars_by_name.get(type_name)
    if scalar_definition is not None and scalar_definition.serialize is not None:
        return scalar_definition.serialize(value)
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, (decimal.Decimal, uuid.UUID)):
        return str(value)
    return value


def _resolve_concrete_type(type_name: str, raw_value: Any, schema: "Schema", info: Info) -> type:
    """Determines which concrete Python class a resolved abstract (interface/union) value is,
    per §4/§5 -- `is_type_of` for an interface's implementors, `resolve_type` (or `isinstance`
    fallback) for a union's members. A plain object type is already concrete; this is only reached
    for one that isn't (see `_complete_value`'s NAMED branch).
    """
    type_class = schema.types_by_name.get(type_name)
    if type_class is not None:
        if type_class.__bramble_type_info__.kind != "interface":
            return type_class
        candidates = schema.implementors_by_interface.get(type_name, [])
        return resolve_interface_type(candidates, raw_value, info)

    members = schema.union_members_by_name.get(type_name)
    if members is not None:
        marker = schema.union_markers_by_name.get(type_name)
        return resolve_union_type(marker, members, raw_value, info)

    raise GraphQLError(
        f"'{type_name}' is not a registered object, interface, or union type",
        code=ErrorCode.INTERFACE_TYPE_RESOLUTION_FAILED,
    )


def _applicable_selections(
    selections: Sequence["LoweredField"], concrete_type: type, schema: "Schema"
) -> list["LoweredField"]:
    """Filters selections already flattened past fragment spreads/inline fragments down to the
    ones that apply to `concrete_type`: unconditional fields (no `type_condition`, written
    directly against the field's own declared type) always apply; a field scoped to a fragment's
    `type_condition` only applies if `concrete_type` *is* that type, implements it (interface), or
    is one of its members (union).
    """
    concrete_name = concrete_type.__bramble_type_info__.name
    result = []
    for selection in selections:
        condition = selection.type_condition
        if condition is None or condition == concrete_name:
            result.append(selection)
            continue
        if concrete_type in schema.implementors_by_interface.get(condition, ()):
            result.append(selection)
            continue
        if concrete_type in schema.union_members_by_name.get(condition, ()):
            result.append(selection)
    return result


def _find_field_info(concrete_type: type, field_name: str, *, auto_camel_case: bool) -> "FieldInfo | None":
    for field_info in concrete_type.__bramble_type_info__.fields:
        graphql_key = _effective_name(field_info.name, field_info.graphql_name, auto_camel_case=auto_camel_case)
        if graphql_key == field_name:
            return field_info
    return None


def _build_error(message: str, path: Path, lowered_field: "LoweredField") -> GraphQLError:
    return GraphQLError(
        message,
        code=ErrorCode.FIELD_RESOLUTION_FAILED,
        path=path.as_list(),
        locations=[(lowered_field.line, lowered_field.column)],
    )


def _error_from_exception(error: Exception, path: Path, lowered_field: "LoweredField") -> GraphQLError:
    """A resolver that deliberately raises its own `bramble.GraphQLError` (a custom `code`,
    `extensions`, etc.) keeps all of that -- only `path`/`locations` are overwritten, since the
    resolver has no way to know its own position in the response ahead of time. Any other
    exception is wrapped generically (`_build_error`), same as an unexpected bug in resolver code.
    """
    if isinstance(error, GraphQLError):
        error.path = path.as_list()
        error.locations = [(lowered_field.line, lowered_field.column)]
        return error
    return _build_error(str(error), path, lowered_field)


async def _complete_value(
    *,
    type_info: "GraphQLTypeInfo",
    raw_value: Any,
    lowered_field: "LoweredField",
    python_name: str,
    selections: Sequence["LoweredField"],
    path: Path,
    state: _ExecutionState,
) -> Any:
    """Implements GraphQL's `CompleteValue` (§8/§11): unwraps `NonNull`/`List` recursively,
    recurses into a nested selection set for object/interface/union types (dispatching the
    concrete type first for the latter two), and serializes scalar leaves. Raises
    `_PropagateNull` when a non-null boundary is violated, after recording the responsible error --
    the caller (a list item's loop, or `_execute_field`) decides from there whether it must keep
    propagating (its own slot is also non-null) or can absorb the failure as `None`.

    `selections` is `lowered_field`'s sub-selections *merged* across every occurrence of this
    response key (§8's `CollectFields`) -- kept as a separate parameter rather than always reading
    `lowered_field.selections` directly, since `lowered_field` here is only ever the *first*
    occurrence (used for identity: field name, arguments, directives), not the full merged set.
    """
    if type_info.kind == "NON_NULL":
        if raw_value is None:
            state.errors.append(_build_error("Cannot return null for non-nullable field.", path, lowered_field))
            raise _PropagateNull
        return await _complete_value(
            type_info=type_info.of_type,
            raw_value=raw_value,
            python_name=python_name,
            lowered_field=lowered_field,
            selections=selections,
            path=path,
            state=state,
        )

    if raw_value is None:
        return None

    if type_info.kind == "LIST":

        async def _complete_item(index: int, item: Any) -> Any:
            return await _complete_value(
                type_info=type_info.of_type,
                raw_value=item,
                python_name=python_name,
                lowered_field=lowered_field,
                selections=selections,
                path=Path(key=index, prev=path),
                state=state,
            )

        # List items may complete concurrently (the spec never requires ordering here, only that
        # the *response* array preserves each item's position -- `asyncio.gather` already returns
        # outcomes in the same order as the awaitables passed in, regardless of completion order).
        # `return_exceptions=True` so one item's `_PropagateNull` doesn't cancel its siblings --
        # every item still gets to run (and record its own errors) before this list decides
        # whether *it* must propagate too.
        outcomes = await asyncio.gather(
            *(_complete_item(index, item) for index, item in enumerate(raw_value)), return_exceptions=True
        )

        item_type_non_null = type_info.of_type.kind == "NON_NULL"
        results = []
        propagate: _PropagateNull | None = None
        for outcome in outcomes:
            if isinstance(outcome, _PropagateNull):
                if item_type_non_null:
                    propagate = propagate or outcome
                results.append(None)
                continue
            if isinstance(outcome, BaseException):
                raise outcome
            results.append(outcome)
        if propagate is not None:
            raise propagate
        return results

    type_name = type_info.name
    assert type_name is not None  # NAMED is the only remaining kind, and it always carries a name.

    # An enum is a leaf, not a composite: it has members rather than fields, so it never enters
    # selection-set execution below -- the resolved Python member is serialized straight to the
    # GraphQL name a client matches on.
    named_type = state.schema.types_by_name.get(type_name)
    if named_type is not None and named_type.__bramble_type_info__.kind == "enum":
        return _enum_graphql_name(named_type, raw_value)

    if type_name in state.schema.types_by_name or type_name in state.schema.union_members_by_name:
        info = _build_info(
            field_name=lowered_field.field_name,
            python_name=python_name,
            path=path,
            selections=selections,
            state=state,
        )
        concrete_type = _resolve_concrete_type(type_name, raw_value, state.schema, info)
        return await _execute_selection_set(
            selections=selections,
            concrete_type=concrete_type,
            parent_value=raw_value,
            path=path,
            state=state,
        )

    return _serialize_scalar(type_name, raw_value, state.schema)


async def _finish_field(
    *,
    field_info: "FieldInfo",
    lowered_field: "LoweredField",
    selections: Sequence["LoweredField"],
    raw_value: Any,
    path: Path,
    state: _ExecutionState,
    info: Info,
) -> Any:
    """The part of field execution that happens *after* a raw value is already in hand (custom
    directives, then `CompleteValue`) -- shared by `_execute_field` (whose raw value comes from
    calling the field's own resolver) and subscription event dispatch (§ subscriptions, whose raw
    value is instead each event a subscription resolver's async generator yields; that resolver is
    only ever called once, to create the stream, never per event -- there's no second "resolve"
    step to run here, only completion of the event itself as the field's value). `info` is always
    the same one built for this field itself (rebuilt per event for a subscription, since each
    event's own errors need their own scope, but describing the same field/path/selections every
    time) -- passed through to `_apply_custom_directives` for a directive's own `Info`/`Depends[T]`
    parameters (§3c).
    """
    is_non_null = field_info.type_info.kind == "NON_NULL"

    try:
        raw_value = await _apply_custom_directives(
            lowered_field.directives, raw_value, state.schema, info=info, scope=state.dependency_scope
        )
    except _PropagateNull:
        raise
    except Exception as error:  # noqa: BLE001 -- deliberately broad: any directive failure becomes a field error, per §8.
        state.errors.append(_error_from_exception(error, path, lowered_field))
        if is_non_null:
            raise _PropagateNull from error
        return None

    try:
        return await _complete_value(
            type_info=field_info.type_info,
            raw_value=raw_value,
            python_name=field_info.name,
            lowered_field=lowered_field,
            selections=selections,
            path=path,
            state=state,
        )
    except _PropagateNull:
        if is_non_null:
            raise
        return None


async def _execute_field(
    *,
    field_info: "FieldInfo",
    lowered_field: "LoweredField",
    selections: Sequence["LoweredField"],
    parent_value: Any,
    concrete_type: type,
    path: Path,
    state: _ExecutionState,
) -> Any:
    is_non_null = field_info.type_info.kind == "NON_NULL"
    info = _build_info(
        field_name=lowered_field.field_name,
        python_name=field_info.name,
        path=path,
        selections=selections,
        state=state,
    )

    try:
        raw_value = await _resolve_field_value(
            field_info, lowered_field, parent_value, info, concrete_type, state.schema, state.dependency_scope
        )
    except _PropagateNull:
        raise
    except Exception as error:  # noqa: BLE001 -- deliberately broad: any resolver failure becomes a field error, per §8.
        state.errors.append(_error_from_exception(error, path, lowered_field))
        if is_non_null:
            raise _PropagateNull from error
        return None

    return await _finish_field(
        field_info=field_info,
        lowered_field=lowered_field,
        selections=selections,
        raw_value=raw_value,
        path=path,
        state=state,
        info=info,
    )


def _group_by_response_key(selections: Sequence["LoweredField"]) -> dict[str, list["LoweredField"]]:
    """Implements GraphQL's `CollectFields` merge (§8): the same response key can legally appear
    more than once at one nesting level -- once directly, and/or again via one or more applicable
    fragments -- and every occurrence's sub-selections must be merged, not have a later occurrence
    silently overwrite an earlier one's data. The first occurrence's own identity (field name,
    arguments, directives) is used for the merged field; bramble doesn't validate that repeated
    occurrences actually agree on these (an accepted approximation, matching
    `check_value_matches_type`'s own documented scope elsewhere) -- only their selections merge.
    """
    groups: dict[str, list["LoweredField"]] = {}
    for selection in selections:
        groups.setdefault(selection.response_key, []).append(selection)
    return groups


async def _execute_selection_set(
    *,
    selections: Sequence["LoweredField"],
    concrete_type: type,
    parent_value: Any,
    path: Path | None,
    state: _ExecutionState,
    serial: bool = False,
) -> dict[str, Any]:
    """Executes one selection set against a known concrete type + resolved parent value (§8's
    per-field algorithm), after merging any same-response-key occurrences (`_group_by_response_key`).
    Fields run concurrently by default (`asyncio.gather`) -- the spec permits but doesn't require
    this, and it lets I/O-bound resolvers actually overlap. `serial=True` is passed only for a
    mutation's *root* selection set (the one spec-mandated exception: "fields of the top-level
    selection set must be executed serially"); anything nested -- including inside a mutation's own
    result, or a list's items -- reverts to concurrent execution regardless of the root operation.
    Raises `_PropagateNull` if the *entire* selection set must be discarded, i.e. one of its own
    non-null fields propagated up to here.
    """
    grouped = _group_by_response_key(_applicable_selections(selections, concrete_type, state.schema))

    async def _resolve_group(response_key: str, occurrences: list["LoweredField"]) -> tuple[str, Any]:
        primary = occurrences[0]

        if primary.field_name == "__typename":
            return response_key, concrete_type.__bramble_type_info__.name

        field_info = _find_field_info(
            concrete_type, primary.field_name, auto_camel_case=state.schema.config.auto_camel_case
        )
        if field_info is None:
            raise GraphQLError(
                f"field '{primary.field_name}' does not exist on type "
                f"'{concrete_type.__bramble_type_info__.name}'",
                code=ErrorCode.UNKNOWN_FIELD,
            )

        merged_selections = [selection for occurrence in occurrences for selection in occurrence.selections]
        field_path = Path(key=response_key, prev=path)
        value = await _execute_field(
            field_info=field_info,
            lowered_field=primary,
            selections=merged_selections,
            parent_value=parent_value,
            concrete_type=concrete_type,
            path=field_path,
            state=state,
        )
        return response_key, value

    if serial:
        result: dict[str, Any] = {}
        for response_key, occurrences in grouped.items():
            key, value = await _resolve_group(response_key, occurrences)
            result[key] = value
        return result

    # `_execute_field` already decides internally (per field, based on *that field's* own type)
    # whether a failure should propagate past it or be absorbed as `None` -- so unlike the list-item
    # case above, there's no further "should I absorb this here" check: any `_PropagateNull` that
    # reaches this level always means the whole selection set must propagate too. Still gathered
    # with `return_exceptions=True` so every sibling field gets to run (and record its own errors)
    # before that decision is acted on.
    outcomes = await asyncio.gather(
        *(_resolve_group(response_key, occurrences) for response_key, occurrences in grouped.items()),
        return_exceptions=True,
    )

    result = {}
    propagate: _PropagateNull | None = None
    for outcome in outcomes:
        if isinstance(outcome, _PropagateNull):
            propagate = propagate or outcome
            continue
        if isinstance(outcome, BaseException):
            raise outcome
        key, value = outcome
        result[key] = value
    if propagate is not None:
        raise propagate
    return result


# --- @defer/@stream incremental delivery ---------------------------------------------------------
#
# Targets the *simpler* `path`/`data`/`hasNext` payload shape (not the newer `pending`/`id`/
# `completed` tracking revision some other implementations have moved to) -- see the roadmap's own
# scope notes for why. Two deliberate scope narrowings, both "fall back to eager/immediate
# resolution" rather than silently wrong or hard-erroring:
#   - `@defer` requires *exclusive* fields (already enforced during lowering, `LoweredField.is_deferred`
#     is only ever set when nothing else at that same level also selects that response key) --
#     the real spec's defer-aware `CollectFields` merge (which would still defer a field selected by
#     two *different* deferred fragments, combining their labels) isn't implemented.
#   - `@stream` requires the field's own resolver to already be an async generator (mirroring
#     `Schema.subscribe_async`'s identical requirement of a subscription root field) -- not a
#     generic "chunk any already-resolved `list[T]`" mechanism.


@dataclass
class _JobTracker:
    """Tracks in-flight `@defer`/`@stream` background jobs for one `execute_incremental` call -- a
    plain mutable counter, no lock needed: asyncio is single-threaded/cooperative, and every
    increment/decrement here happens in a stretch of code with no `await` in between, so there's no
    interleaving risk. Incremented synchronously right before a job's own `asyncio.Task` is created,
    decremented by the job itself only once its own work is truly finished (a deferred job: after
    its one-shot resolve; a streamed job: after its generator is fully drained) -- a job that itself
    discovers *more* deferred/streamed markers while running increments this further before
    decrementing for itself, so the count never touches zero while real work remains, even nested.

    `any_spawned` is a separate, monotonic (set once, never unset) flag alongside `outstanding` --
    a job with no real async I/O of its own (a toy resolver, say) can run to completion, put its
    patch on the queue, and decrement `outstanding` back to `0` *before* `execute_incremental`'s own
    frame gets back around to checking it (`asyncio.create_task` only schedules a task to run at the
    next opportunity, and "the next opportunity" can arrive well before control returns to the code
    that created it). Checking `outstanding` directly for "should the initial payload's own
    `hasNext` be true" or "should I bother draining the queue at all" would then wrongly see `0` even
    though a real patch is already sitting in the queue -- `any_spawned` sidesteps that race
    entirely, since it's read-only from that point on.
    """

    outstanding: int = 0
    any_spawned: bool = False


@dataclass(frozen=True, slots=True)
class _IncrementalContext:
    """Threaded through the incremental-aware execution call chain alongside `_ExecutionState`:
    where a background job's own completed patch gets sent, and how many jobs are still in flight.
    """

    patch_queue: "asyncio.Queue[dict[str, Any]]"
    tracker: _JobTracker


def _path_as_list(path: Path | None) -> list[str | int]:
    return path.as_list() if path is not None else []


def _has_incremental_markers(selections: Sequence["LoweredField"]) -> bool:
    """Whether `selections` (or anything nested under them) contains an active `@defer`/`@stream`
    marker -- decides whether a query needs `execute_incremental` at all. `execute_async` stays a
    thin, zero-overhead delegate to the plain (non-incremental) path for the overwhelmingly common
    case where it doesn't.
    """
    for selection in selections:
        if selection.is_deferred or selection.is_streamed:
            return True
        if _has_incremental_markers(selection.selections):
            return True
    return False


async def _execute_field_incremental(
    *,
    field_info: "FieldInfo",
    lowered_field: "LoweredField",
    selections: Sequence["LoweredField"],
    parent_value: Any,
    concrete_type: type,
    path: Path,
    state: _ExecutionState,
    incremental: _IncrementalContext,
) -> Any:
    """`_execute_field`'s incremental-aware sibling -- identical resolver-calling/error-handling,
    just threading `incremental` through to `_finish_field_incremental` so a nested object
    selection set can keep discovering further `@defer`/`@stream` markers arbitrarily deep.
    """
    is_non_null = field_info.type_info.kind == "NON_NULL"
    info = _build_info(
        field_name=lowered_field.field_name,
        python_name=field_info.name,
        path=path,
        selections=selections,
        state=state,
    )

    try:
        raw_value = await _resolve_field_value(
            field_info, lowered_field, parent_value, info, concrete_type, state.schema, state.dependency_scope
        )
    except _PropagateNull:
        raise
    except Exception as error:  # noqa: BLE001 -- deliberately broad: any resolver failure becomes a field error, per §8.
        state.errors.append(_error_from_exception(error, path, lowered_field))
        if is_non_null:
            raise _PropagateNull from error
        return None

    return await _finish_field_incremental(
        field_info=field_info,
        lowered_field=lowered_field,
        selections=selections,
        raw_value=raw_value,
        path=path,
        state=state,
        incremental=incremental,
        info=info,
    )


async def _finish_field_incremental(
    *,
    field_info: "FieldInfo",
    lowered_field: "LoweredField",
    selections: Sequence["LoweredField"],
    raw_value: Any,
    path: Path,
    state: _ExecutionState,
    incremental: _IncrementalContext,
    info: Info,
) -> Any:
    is_non_null = field_info.type_info.kind == "NON_NULL"

    try:
        raw_value = await _apply_custom_directives(
            lowered_field.directives, raw_value, state.schema, info=info, scope=state.dependency_scope
        )
    except _PropagateNull:
        raise
    except Exception as error:  # noqa: BLE001 -- deliberately broad: any directive failure becomes a field error, per §8.
        state.errors.append(_error_from_exception(error, path, lowered_field))
        if is_non_null:
            raise _PropagateNull from error
        return None

    try:
        return await _complete_value_incremental(
            type_info=field_info.type_info,
            raw_value=raw_value,
            python_name=field_info.name,
            lowered_field=lowered_field,
            selections=selections,
            path=path,
            state=state,
            incremental=incremental,
        )
    except _PropagateNull:
        if is_non_null:
            raise
        return None


async def _complete_value_incremental(
    *,
    type_info: "GraphQLTypeInfo",
    raw_value: Any,
    lowered_field: "LoweredField",
    python_name: str,
    selections: Sequence["LoweredField"],
    path: Path,
    state: _ExecutionState,
    incremental: _IncrementalContext,
) -> Any:
    """`_complete_value`'s incremental-aware sibling -- identical `NonNull`/`List`/scalar handling;
    only the object/interface/union recursion differs, calling `_execute_selection_set_incremental`
    instead of `_execute_selection_set` so a nested selection set can itself discover further
    `@defer`/`@stream` markers.
    """
    if type_info.kind == "NON_NULL":
        if raw_value is None:
            state.errors.append(_build_error("Cannot return null for non-nullable field.", path, lowered_field))
            raise _PropagateNull
        return await _complete_value_incremental(
            type_info=type_info.of_type,
            raw_value=raw_value,
            python_name=python_name,
            lowered_field=lowered_field,
            selections=selections,
            path=path,
            state=state,
            incremental=incremental,
        )

    if raw_value is None:
        return None

    if type_info.kind == "LIST":

        async def _complete_item(index: int, item: Any) -> Any:
            return await _complete_value_incremental(
                type_info=type_info.of_type,
                raw_value=item,
                python_name=python_name,
                lowered_field=lowered_field,
                selections=selections,
                path=Path(key=index, prev=path),
                state=state,
                incremental=incremental,
            )

        outcomes = await asyncio.gather(
            *(_complete_item(index, item) for index, item in enumerate(raw_value)), return_exceptions=True
        )

        item_type_non_null = type_info.of_type.kind == "NON_NULL"
        results = []
        propagate: _PropagateNull | None = None
        for outcome in outcomes:
            if isinstance(outcome, _PropagateNull):
                if item_type_non_null:
                    propagate = propagate or outcome
                results.append(None)
                continue
            if isinstance(outcome, BaseException):
                raise outcome
            results.append(outcome)
        if propagate is not None:
            raise propagate
        return results

    type_name = type_info.name
    assert type_name is not None  # NAMED is the only remaining kind, and it always carries a name.

    # An enum is a leaf, not a composite: it has members rather than fields, so it never enters
    # selection-set execution below -- the resolved Python member is serialized straight to the
    # GraphQL name a client matches on.
    named_type = state.schema.types_by_name.get(type_name)
    if named_type is not None and named_type.__bramble_type_info__.kind == "enum":
        return _enum_graphql_name(named_type, raw_value)

    if type_name in state.schema.types_by_name or type_name in state.schema.union_members_by_name:
        info = _build_info(
            field_name=lowered_field.field_name,
            python_name=python_name,
            path=path,
            selections=selections,
            state=state,
        )
        concrete_type = _resolve_concrete_type(type_name, raw_value, state.schema, info)
        return await _execute_selection_set_incremental(
            selections=selections,
            concrete_type=concrete_type,
            parent_value=raw_value,
            path=path,
            state=state,
            incremental=incremental,
        )

    return _serialize_scalar(type_name, raw_value, state.schema)


async def _start_streamed_field(
    *,
    field_info: "FieldInfo",
    lowered_field: "LoweredField",
    selections: Sequence["LoweredField"],
    parent_value: Any,
    concrete_type: type,
    path: Path,
    state: _ExecutionState,
    incremental: _IncrementalContext,
) -> list[Any]:
    """Starts a `@stream`-marked field: calls its resolver (which must itself be an async generator
    -- the same convention `Schema.subscribe_async` already requires of a subscription root field's
    own resolver), eagerly consumes exactly `initialCount` items for the initial payload, then hands
    the still-open generator to a background job delivering the rest as incremental `items` patches,
    one per item.
    """
    # Pessimistically reserve a background-job slot in `outstanding` *before* any real `await`
    # happens below (the resolver call, item consumption) -- undone further down if it turns out
    # unnecessary (the generator exhausts during eager consumption). A sibling stream field with no
    # real async work of its own could otherwise run to completion and decrement `outstanding` back
    # to `0` before *this* field even finishes deciding whether it needs a job of its own -- see
    # `_JobTracker`'s own docstring for the general shape of this race.
    #
    # Deliberately does NOT set `any_spawned` yet -- unlike `outstanding` (which must be
    # pessimistic to close the sibling race above), `any_spawned` must stay `False` until a job is
    # *genuinely* confirmed (further down), since it's never reset back if the reservation here
    # turns out unneeded; setting it this early caused a real bug (caught by testing): a stream
    # whose `initialCount` covers its entire list, spawning no job at all, still left
    # `any_spawned=True`, so `execute_incremental`'s consumer loop waited forever on a patch that
    # was never coming.
    incremental.tracker.outstanding += 1

    info = _build_info(
        field_name=lowered_field.field_name,
        python_name=field_info.name,
        path=path,
        selections=selections,
        state=state,
    )
    resolver = getattr(concrete_type, field_info.name)
    kwargs = await _bind_resolver_kwargs(
        field_info, lowered_field, parent_value, info, state.schema, resolver, concrete_type, state.dependency_scope
    )
    result = resolver(**kwargs)

    if not inspect.isasyncgen(result):
        incremental.tracker.outstanding -= 1
        if inspect.iscoroutine(result):
            # A plain `async def` resolver (not an async generator) returns an unawaited coroutine
            # here -- close it explicitly, or Python warns about it at GC time.
            result.close()
        raise GraphQLError(
            f"'@stream' field '{lowered_field.field_name}' must be an async generator resolver",
            code=ErrorCode.FIELD_RESOLUTION_FAILED,
        )

    # `field_info.type_info` here is the list field's own declared type -- unwrap exactly one
    # `NON_NULL` (if present) then one `LIST` layer to get each item's own type, the same unwrapping
    # `_complete_value`'s own LIST branch does for a non-streamed list. Validation (Rust, Phase 0)
    # already guarantees this field's declared type really is a list.
    item_type_info = field_info.type_info
    if item_type_info.kind == "NON_NULL":
        item_type_info = item_type_info.of_type
    item_type_info = item_type_info.of_type

    initial_items: list[Any] = []
    index = 0
    initial_count = lowered_field.stream_initial_count or 0
    exhausted = False
    while index < initial_count:
        try:
            item = await result.__anext__()
        except StopAsyncIteration:
            exhausted = True
            break
        try:
            value = await _complete_value_incremental(
                type_info=item_type_info,
                raw_value=item,
                python_name=field_info.name,
                lowered_field=lowered_field,
                selections=selections,
                path=Path(key=index, prev=path),
                state=state,
                incremental=incremental,
            )
        except _PropagateNull:
            value = None
        initial_items.append(value)
        index += 1

    if exhausted:
        incremental.tracker.outstanding -= 1  # undo the pessimistic reservation above -- no job needed
    else:
        incremental.tracker.any_spawned = True  # genuinely confirmed now -- see this function's own note above
        asyncio.create_task(
            _run_streamed_job(
                label=lowered_field.stream_label,
                path=path,
                item_type_info=item_type_info,
                lowered_field=lowered_field,
                python_name=field_info.name,
                selections=selections,
                generator=result,
                next_index=index,
                state=state,
                incremental=incremental,
            )
        )
    return initial_items


async def _run_streamed_job(
    *,
    label: str | None,
    path: Path,
    item_type_info: "GraphQLTypeInfo",
    lowered_field: "LoweredField",
    python_name: str,
    selections: Sequence["LoweredField"],
    generator: AsyncGenerator[Any, None],
    next_index: int,
    state: _ExecutionState,
    incremental: _IncrementalContext,
) -> None:
    """Delivers the rest of a `@stream`-marked field's already-open async generator, one incremental
    `items` patch per item. Looks one item ahead before sending each patch purely to compute that
    patch's own `hasNext` correctly: the simpler payload shape this targets has no separate "this
    stream is now closed" signal, so the *last* item's own patch has to be the one reporting
    `hasNext: false`, which means knowing an item is the last one before sending its patch.

    The generator itself (not just an individual item's own completion) can raise -- unlike a
    plain `_PropagateNull`/completion error on one item (already handled per-item, below, without
    ending the stream), a failure *fetching the next item* has no item to attach the error to and
    ends the stream there. This still has to reach `patch_queue` as a proper terminal patch (never
    just silently stop): the consumer side (`execute_incremental`) only knows a delivery is finished
    once it sees a patch with `hasNext: false`, so a job that vanishes without ever sending one would
    hang that consumer forever.
    """
    index = next_index
    path_list = _path_as_list(path)
    decremented = False

    def _decrement_once() -> None:
        nonlocal decremented
        if not decremented:
            incremental.tracker.outstanding -= 1
            decremented = True

    async def _send_final_error(error: Exception) -> None:
        _decrement_once()
        entry: dict[str, Any] = {
            "items": [],
            "path": path_list,
            "errors": [_error_to_dict(GraphQLError(str(error), code=ErrorCode.FIELD_RESOLUTION_FAILED, path=path_list))],
        }
        if label is not None:
            entry["label"] = label
        await incremental.patch_queue.put({"incremental": [entry], "hasNext": incremental.tracker.outstanding > 0})

    try:
        try:
            current = await generator.__anext__()
        except StopAsyncIteration:
            return
        except Exception as error:  # noqa: BLE001 -- the generator failed before yielding anything; still needs its own terminal patch, not a hang.
            await _send_final_error(error)
            return

        while True:
            next_item_error: Exception | None = None
            try:
                next_item: Any = await generator.__anext__()
                is_last = False
            except StopAsyncIteration:
                next_item = None
                is_last = True
            except Exception as error:  # noqa: BLE001 -- surfaced as a separate terminal patch right after this item's own, below.
                next_item = None
                is_last = True
                next_item_error = error

            item_errors: list[GraphQLError] = []
            item_state = replace(state, errors=item_errors)
            try:
                value = await _complete_value_incremental(
                    type_info=item_type_info,
                    raw_value=current,
                    python_name=python_name,
                    lowered_field=lowered_field,
                    selections=selections,
                    path=Path(key=index, prev=path),
                    state=item_state,
                    incremental=incremental,
                )
            except _PropagateNull:
                value = None

            # Only decrement (and let this patch claim `hasNext: false`) if this item's own patch
            # really is the last one this job will ever send -- when the *next* item's fetch itself
            # failed, one more patch (the error one, via `_send_final_error` below) is still coming
            # from this same job, so this one must not claim finality yet.
            if is_last and next_item_error is None:
                _decrement_once()

            entry: dict[str, Any] = {"items": [value], "path": path_list}
            if label is not None:
                entry["label"] = label
            if item_errors:
                entry["errors"] = [_error_to_dict(error) for error in item_errors]
            await incremental.patch_queue.put({"incremental": [entry], "hasNext": incremental.tracker.outstanding > 0})

            if next_item_error is not None:
                await _send_final_error(next_item_error)
                return
            if is_last:
                return
            current = next_item
            index += 1
    finally:
        _decrement_once()


def _spawn_deferred_jobs(
    deferred_groups: dict[str | None, list[tuple[str, "FieldInfo | None", "LoweredField", list["LoweredField"]]]],
    *,
    path: Path | None,
    parent_value: Any,
    concrete_type: type,
    state: _ExecutionState,
    incremental: _IncrementalContext,
) -> None:
    for label, group_fields in deferred_groups.items():
        incremental.tracker.outstanding += 1
        incremental.tracker.any_spawned = True
        asyncio.create_task(
            _run_deferred_job(
                label=label,
                path=path,
                fields=group_fields,
                parent_value=parent_value,
                concrete_type=concrete_type,
                state=state,
                incremental=incremental,
            )
        )


async def _run_deferred_job(
    *,
    label: str | None,
    path: Path | None,
    fields: list[tuple[str, "FieldInfo", "LoweredField", list["LoweredField"]]],
    parent_value: Any,
    concrete_type: type,
    state: _ExecutionState,
    incremental: _IncrementalContext,
) -> None:
    """Resolves every field exclusive to one `@defer` application (identified by its shared
    `label`, `None` included), merging them into one `data` object delivered as a single incremental
    patch at the deferred fragment's own enclosing path.
    """
    job_errors: list[GraphQLError] = []
    job_state = replace(state, errors=job_errors)
    data: dict[str, Any] | None = {}

    async def _resolve_one(
        response_key: str, field_info: "FieldInfo | None", lowered_field: "LoweredField", selections: list["LoweredField"]
    ) -> tuple[str, Any]:
        if lowered_field.field_name == "__typename":
            return response_key, concrete_type.__bramble_type_info__.name

        assert field_info is not None
        field_path = Path(key=response_key, prev=path)
        value = await _execute_field_incremental(
            field_info=field_info,
            lowered_field=lowered_field,
            selections=selections,
            parent_value=parent_value,
            concrete_type=concrete_type,
            path=field_path,
            state=job_state,
            incremental=incremental,
        )
        return response_key, value

    try:
        outcomes = await asyncio.gather(*(_resolve_one(*entry) for entry in fields), return_exceptions=True)
        propagate = False
        for outcome in outcomes:
            if isinstance(outcome, _PropagateNull):
                propagate = True
                continue
            if isinstance(outcome, BaseException):
                raise outcome
            key, value = outcome
            data[key] = value
        if propagate:
            data = None
    finally:
        incremental.tracker.outstanding -= 1
        entry = {"data": data, "path": _path_as_list(path)}
        if label is not None:
            entry["label"] = label
        if job_errors:
            entry["errors"] = [_error_to_dict(error) for error in job_errors]
        await incremental.patch_queue.put({"incremental": [entry], "hasNext": incremental.tracker.outstanding > 0})


async def _execute_selection_set_incremental(
    *,
    selections: Sequence["LoweredField"],
    concrete_type: type,
    parent_value: Any,
    path: Path | None,
    state: _ExecutionState,
    incremental: _IncrementalContext,
    serial: bool = False,
) -> dict[str, Any]:
    """`_execute_selection_set`'s incremental-delivery-aware sibling: identical field-grouping and
    concurrent-resolution for any field with no active `@defer`/`@stream` marker, but a
    `@defer`-marked field is held back from this selection set's own result entirely -- grouped with
    any sibling deferred fields sharing the same label into one background job (`_spawn_deferred_jobs`)
    -- and a `@stream`-marked field resolves only its own `initialCount` items eagerly
    (`_start_streamed_field`), handing the rest of its resolver's async generator to a second kind
    of background job.
    """
    grouped = _group_by_response_key(_applicable_selections(selections, concrete_type, state.schema))

    deferred_groups: dict[str | None, list[tuple[str, "FieldInfo | None", "LoweredField", list["LoweredField"]]]] = {}
    resolvable: list[tuple[str, "FieldInfo", "LoweredField", list["LoweredField"]]] = []

    # A synchronous classification pass -- no `await` anywhere in this loop -- so every deferred
    # field at this level is grouped (by label) *before* any sibling field, deferred or not, starts
    # actually resolving. `_spawn_deferred_jobs` right after this loop then registers all of them
    # with `incremental.tracker` before the eager/stream gather below even begins: without this
    # ordering, a `@stream` field with no real async work of its own could run to completion and
    # decrement `outstanding` back to `0` before a slower-to-register deferred sibling is even known
    # to exist, ending the whole delivery early (see `_JobTracker`'s own docstring).
    for response_key, occurrences in grouped.items():
        primary = occurrences[0]

        if primary.field_name == "__typename":
            if primary.is_deferred:
                deferred_groups.setdefault(primary.defer_label, []).append(
                    (response_key, None, primary, [])  # type: ignore[arg-type]
                )
            else:
                resolvable.append((response_key, None, primary, []))  # type: ignore[arg-type]
            continue

        field_info = _find_field_info(
            concrete_type, primary.field_name, auto_camel_case=state.schema.config.auto_camel_case
        )
        if field_info is None:
            raise GraphQLError(
                f"field '{primary.field_name}' does not exist on type "
                f"'{concrete_type.__bramble_type_info__.name}'",
                code=ErrorCode.UNKNOWN_FIELD,
            )

        merged_selections = [selection for occurrence in occurrences for selection in occurrence.selections]

        if primary.is_deferred:
            deferred_groups.setdefault(primary.defer_label, []).append(
                (response_key, field_info, primary, merged_selections)
            )
            continue

        resolvable.append((response_key, field_info, primary, merged_selections))

    _spawn_deferred_jobs(
        deferred_groups, path=path, parent_value=parent_value, concrete_type=concrete_type, state=state, incremental=incremental
    )

    async def _resolve_group(
        response_key: str, field_info: "FieldInfo | None", primary: "LoweredField", merged_selections: list["LoweredField"]
    ) -> tuple[str, Any]:
        if primary.field_name == "__typename":
            return response_key, concrete_type.__bramble_type_info__.name

        assert field_info is not None
        field_path = Path(key=response_key, prev=path)

        if primary.is_streamed:
            value = await _start_streamed_field(
                field_info=field_info,
                lowered_field=primary,
                selections=merged_selections,
                parent_value=parent_value,
                concrete_type=concrete_type,
                path=field_path,
                state=state,
                incremental=incremental,
            )
            return response_key, value

        value = await _execute_field_incremental(
            field_info=field_info,
            lowered_field=primary,
            selections=merged_selections,
            parent_value=parent_value,
            concrete_type=concrete_type,
            path=field_path,
            state=state,
            incremental=incremental,
        )
        return response_key, value

    if serial:
        result: dict[str, Any] = {}
        for response_key, field_info, primary, merged_selections in resolvable:
            key, value = await _resolve_group(response_key, field_info, primary, merged_selections)
            result[key] = value
        return result

    outcomes = await asyncio.gather(
        *(_resolve_group(response_key, field_info, primary, merged_selections) for response_key, field_info, primary, merged_selections in resolvable),
        return_exceptions=True,
    )
    result = {}
    propagate = None
    for outcome in outcomes:
        if isinstance(outcome, _PropagateNull):
            propagate = propagate or outcome
            continue
        if isinstance(outcome, BaseException):
            raise outcome
        key, value = outcome
        result[key] = value
    if propagate is not None:
        raise propagate
    return result


_ROOT_TYPE_ATTRIBUTE_BY_OPERATION = {
    # `query_root`, not `query`: the former is the subclass carrying the injected `__schema`/
    # `__type` introspection meta-fields, while `Schema.query` stays the caller's own class.
    "query": "query_root",
    "mutation": "mutation",
    "subscription": "subscription",
}


def _resolve_execution_context(schema: "Schema", context: Any) -> Any:
    if context is None and schema.execution_context_class is not None:
        return schema.execution_context_class()
    return context


async def execute_async(
    schema: "Schema",
    query: str,
    *,
    variable_values: dict[str, Any] | None = None,
    context: Any = None,
    root_value: Any = None,
    operation_name: str | None = None,
    resolved_dependencies: dict[Callable[..., Any], Any] | None = None,
) -> dict[str, Any]:
    """Executes `query` against `schema` (§7a/§8/§11): validates and lowers it, then walks the
    result with full null-bubbling. A malformed query, a schema-shape validation failure, or an
    unknown operation all raise `bramble.GraphQLError` directly -- consistent with
    `Schema.validate_query`/`resolve_persisted_query`'s own behavior, these are request-level
    failures, not partial-response ones. Once execution actually starts, a resolver/completion
    failure instead becomes an entry in the returned `errors` list, per spec.

    `resolved_dependencies` (§3c) pre-seeds this request's own dependency-injection cache, keyed by
    provider-callable identity -- a value supplied this way skips its provider entirely (never
    invoked, and its own teardown is never run by bramble, which never owned it to begin with).
    """
    resolved_variable_values = variable_values or {}

    validate_query(query, schema._compiled, operation_name)
    operation_type, fields = lower_query(query, variable_values=resolved_variable_values, operation_name=operation_name)

    if operation_type == "subscription":
        raise GraphQLError(
            "execute_async cannot run a subscription operation -- use Schema.subscribe_async instead",
            code=ErrorCode.GRAPHQL_VALIDATION_FAILED,
        )

    if _has_incremental_markers(fields):
        raise GraphQLError(
            "execute_async cannot run a query/mutation using @defer/@stream -- use "
            "Schema.execute_incremental instead",
            code=ErrorCode.GRAPHQL_VALIDATION_FAILED,
        )

    root_type = getattr(schema, _ROOT_TYPE_ATTRIBUTE_BY_OPERATION[operation_type])
    if root_type is None:
        raise GraphQLError(f"schema has no {operation_type} type", code=ErrorCode.GRAPHQL_VALIDATION_FAILED)

    context = _resolve_execution_context(schema, context)

    scope = DependencyScope()
    scope.seed(resolved_dependencies)
    errors: list[GraphQLError] = []
    state = _ExecutionState(
        schema=schema,
        context=context,
        root_value=root_value,
        variable_values=resolved_variable_values,
        query=query,
        errors=errors,
        dependency_scope=scope,
    )

    try:
        try:
            data: dict[str, Any] | None = await _execute_selection_set(
                selections=fields,
                concrete_type=root_type,
                parent_value=root_value,
                path=None,
                state=state,
                serial=operation_type == "mutation",
            )
        except _PropagateNull:
            data = None
    finally:
        await scope.aclose()

    response: dict[str, Any] = {"data": data}
    if errors:
        response["errors"] = [_error_to_dict(error) for error in errors]
    return response


def execute(
    schema: "Schema",
    query: str,
    *,
    variable_values: dict[str, Any] | None = None,
    context: Any = None,
    root_value: Any = None,
    operation_name: str | None = None,
    resolved_dependencies: dict[Callable[..., Any], Any] | None = None,
) -> dict[str, Any]:
    """Synchronous convenience wrapper around `execute_async` for schemas whose resolvers are all
    synchronous. Uses `asyncio.run`, so (like that function) it cannot be called from within an
    already-running event loop -- call `execute_async` directly there instead.
    """
    return asyncio.run(
        execute_async(
            schema,
            query,
            variable_values=variable_values,
            context=context,
            root_value=root_value,
            operation_name=operation_name,
            resolved_dependencies=resolved_dependencies,
        )
    )


async def execute_incremental(
    schema: "Schema",
    query: str,
    *,
    variable_values: dict[str, Any] | None = None,
    context: Any = None,
    root_value: Any = None,
    operation_name: str | None = None,
    resolved_dependencies: dict[Callable[..., Any], Any] | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """Executes a query/mutation operation using `@defer`/`@stream`, yielding one spec-shaped
    payload at a time: the initial `{"data": ..., "hasNext": bool}` (deferred subtrees omitted,
    streamed lists truncated to their own `initialCount`), then zero or more
    `{"incremental": [...], "hasNext": bool}` patches as each deferred fragment resolves or each
    streamed item becomes available -- see this module's own "@defer/@stream incremental delivery"
    section header for the concrete subset of the spec this implements (payload shape,
    defer-exclusivity, stream-requires-async-generator-resolver).

    A query/mutation with *no* active `@defer`/`@stream` marker still works here (degenerating to a
    single `hasNext: false` payload identical to what `execute_async` would produce), but
    `execute_async` is the zero-overhead path for that overwhelmingly common case -- this function
    is for when at least one marker is actually present.

    `resolved_dependencies` (§3c) -- see `execute_async`'s own docstring; this request's dependency
    cache spans the whole incremental delivery, including every deferred/streamed background job,
    not just the initial payload.
    """
    resolved_variable_values = variable_values or {}

    validate_query(query, schema._compiled, operation_name)
    operation_type, fields = lower_query(query, variable_values=resolved_variable_values, operation_name=operation_name)

    if operation_type not in ("query", "mutation"):
        raise GraphQLError(
            "execute_incremental only supports query/mutation operations",
            code=ErrorCode.GRAPHQL_VALIDATION_FAILED,
        )

    root_type = getattr(schema, _ROOT_TYPE_ATTRIBUTE_BY_OPERATION[operation_type])
    if root_type is None:
        raise GraphQLError(f"schema has no {operation_type} type", code=ErrorCode.GRAPHQL_VALIDATION_FAILED)

    context = _resolve_execution_context(schema, context)

    scope = DependencyScope()
    scope.seed(resolved_dependencies)
    errors: list[GraphQLError] = []
    state = _ExecutionState(
        schema=schema,
        context=context,
        root_value=root_value,
        variable_values=resolved_variable_values,
        query=query,
        errors=errors,
        dependency_scope=scope,
    )
    incremental = _IncrementalContext(patch_queue=asyncio.Queue(), tracker=_JobTracker())

    try:
        try:
            data: dict[str, Any] | None = await _execute_selection_set_incremental(
                selections=fields,
                concrete_type=root_type,
                parent_value=root_value,
                path=None,
                state=state,
                incremental=incremental,
                serial=operation_type == "mutation",
            )
        except _PropagateNull:
            data = None

        # `any_spawned` (not `tracker.outstanding`, which may have already raced back down to `0`
        # -- see `_JobTracker`'s own docstring) is what correctly answers "does more data follow".
        initial: dict[str, Any] = {"data": data, "hasNext": incremental.tracker.any_spawned}
        if errors:
            initial["errors"] = [_error_to_dict(error) for error in errors]
        yield initial

        if not incremental.tracker.any_spawned:
            return

        # Each patch's own `hasNext` (computed by the job that produced it, at the exact
        # synchronous moment its own completion was decided) is the authoritative "is this the
        # last one" signal -- not another read of `tracker.outstanding` here, for the same
        # race-avoidance reason as above.
        while True:
            patch = await incremental.patch_queue.get()
            yield patch
            if not patch.get("hasNext", False):
                return
    finally:
        await scope.aclose()


async def subscribe_async(
    schema: "Schema",
    query: str,
    *,
    variable_values: dict[str, Any] | None = None,
    context: Any = None,
    root_value: Any = None,
    operation_name: str | None = None,
    resolved_dependencies: dict[Callable[..., Any], Any] | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """Executes a subscription operation, yielding one spec-shaped `{"data": ..., "errors": [...]}`
    response per event. Per the GraphQL spec's two-phase model: `CreateSourceEventStream` (the
    subscription root field's own resolver, called exactly once, must itself be an async generator
    yielding raw "source events" -- unlike a query/mutation resolver, it's never awaited for a
    single value) and `MapSourceToResponseEvent` (each event re-enters normal field completion via
    `_finish_field`, treating the event itself as if it were that field's already-resolved value --
    there is no second "resolve" call per event, only completion of what was yielded).

    A subscription operation's root selection set must have exactly one field (spec-mandated, not
    just a bramble convention) -- checked here since `validate_query` (Rust) doesn't yet enforce
    this rule. An error raised by the source generator itself (creating or iterating the stream)
    propagates out of this generator entirely (a stream-level failure); an error confined to
    completing one single event becomes that event's own `errors` entry instead, without ending
    the subscription -- one bad event shouldn't kill the stream.

    `resolved_dependencies` (§3c) seeds one dependency-injection cache scoped to this subscription's
    own lifetime -- created once here (reused by the root resolver call that creates the source
    stream, and by every event's own field completion), torn down exactly once when this generator
    itself ends, via `finally` below: normal completion (the source stream itself ends), the
    consumer unsubscribing/disconnecting (`GeneratorExit`, raised by `.aclose()` on this generator
    at whatever `await`/`yield` point it's suspended at), or an error propagating out. Deliberately
    *not* per-connection (a connection can carry multiple concurrent subscriptions, which must not
    share dependency instances) and *not* per-emitted-event (which would re-run every provider on
    every single message).
    """
    resolved_variable_values = variable_values or {}

    validate_query(query, schema._compiled, operation_name)
    operation_type, fields = lower_query(query, variable_values=resolved_variable_values, operation_name=operation_name)

    if operation_type != "subscription":
        raise GraphQLError(
            "subscribe_async can only run a subscription operation -- use Schema.execute_async for query/mutation",
            code=ErrorCode.GRAPHQL_VALIDATION_FAILED,
        )

    root_type = schema.subscription
    if root_type is None:
        raise GraphQLError("schema has no subscription type", code=ErrorCode.GRAPHQL_VALIDATION_FAILED)

    context = _resolve_execution_context(schema, context)

    grouped = _group_by_response_key(_applicable_selections(fields, root_type, schema))
    if len(grouped) != 1:
        raise GraphQLError(
            "a subscription operation must have exactly one root-level field",
            code=ErrorCode.GRAPHQL_VALIDATION_FAILED,
        )
    ((response_key, occurrences),) = grouped.items()
    primary = occurrences[0]
    merged_selections = [selection for occurrence in occurrences for selection in occurrence.selections]
    field_path = Path(key=response_key, prev=None)

    if primary.field_name == "__typename":
        yield {"data": {response_key: root_type.__bramble_type_info__.name}}
        return

    field_info = _find_field_info(root_type, primary.field_name, auto_camel_case=schema.config.auto_camel_case)
    if field_info is None:
        raise GraphQLError(
            f"field '{primary.field_name}' does not exist on type '{root_type.__bramble_type_info__.name}'",
            code=ErrorCode.UNKNOWN_FIELD,
        )

    scope = DependencyScope()
    scope.seed(resolved_dependencies)
    try:
        setup_state = _ExecutionState(
            schema=schema,
            context=context,
            root_value=root_value,
            variable_values=resolved_variable_values,
            query=query,
            errors=[],
            dependency_scope=scope,
        )
        info = _build_info(
            field_name=primary.field_name,
            python_name=field_info.name,
            path=field_path,
            selections=merged_selections,
            state=setup_state,
        )

        resolver = getattr(root_type, field_info.name)
        kwargs = await _bind_resolver_kwargs(field_info, primary, root_value, info, schema, resolver, root_type, scope)
        source_stream = resolver(**kwargs)
        if not inspect.isasyncgen(source_stream):
            if inspect.iscoroutine(source_stream):
                # A plain `async def` resolver (not an async generator) returns an unawaited
                # coroutine here -- close it explicitly, or Python warns about it at GC time.
                source_stream.close()
            raise GraphQLError(
                f"subscription field '{primary.field_name}' must be an async generator resolver",
                code=ErrorCode.FIELD_RESOLUTION_FAILED,
            )

        async for event in source_stream:
            # A fresh errors list per event -- each yielded response reports only its own errors,
            # not ones accumulated from earlier events -- but the *same* dependency scope
            # throughout, so a dependency used across events (or by the root resolver above) is
            # still cached/torn down once for the whole subscription, not once per event.
            event_state = _ExecutionState(
                schema=schema,
                context=context,
                root_value=root_value,
                variable_values=resolved_variable_values,
                query=query,
                errors=[],
                dependency_scope=scope,
            )
            try:
                value = await _finish_field(
                    field_info=field_info,
                    lowered_field=primary,
                    selections=merged_selections,
                    raw_value=event,
                    path=field_path,
                    state=event_state,
                    info=info,
                )
                data: dict[str, Any] | None = {response_key: value}
            except _PropagateNull:
                data = None

            response: dict[str, Any] = {"data": data}
            if event_state.errors:
                response["errors"] = [_error_to_dict(error) for error in event_state.errors]
            yield response
    finally:
        await scope.aclose()
