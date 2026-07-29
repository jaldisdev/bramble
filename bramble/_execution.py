from __future__ import annotations

import asyncio
import datetime
import decimal
import inspect
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from bramble._bramble import lower_query, validate_query
from bramble._error import ErrorCode, GraphQLError
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


def _build_info(
    *,
    field_name: str,
    path: Path,
    selections: Sequence["LoweredField"],
    state: _ExecutionState,
) -> Info:
    info = Info()
    info.field_name = field_name
    info.python_name = field_name
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

    input_type = schema.types_by_name.get(type_name)
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


def _bind_resolver_kwargs(
    field_info: "FieldInfo",
    lowered_field: "LoweredField",
    parent_value: Any,
    info: Info,
    schema: "Schema",
) -> dict[str, Any]:
    kwargs = _map_arguments(field_info.arguments, lowered_field.arguments, auto_camel_case=schema.config.auto_camel_case)
    for argument in field_info.arguments:
        if argument.name in kwargs:
            kwargs[argument.name] = _coerce_value(argument.type_info, kwargs[argument.name], schema)
    if field_info.parent_parameter is not None:
        kwargs[field_info.parent_parameter] = parent_value
    if field_info.info_parameter is not None:
        kwargs[field_info.info_parameter] = info
    return kwargs


async def _resolve_field_value(
    field_info: "FieldInfo",
    lowered_field: "LoweredField",
    parent_value: Any,
    info: Info,
    concrete_type: type,
    schema: "Schema",
) -> Any:
    if not field_info.has_resolver:
        return getattr(parent_value, field_info.name)

    resolver = getattr(concrete_type, field_info.name)
    kwargs = _bind_resolver_kwargs(field_info, lowered_field, parent_value, info, schema)
    result = resolver(**kwargs)
    if inspect.isawaitable(result):
        result = await result
    return result


async def _apply_custom_directives(directives: Sequence[Any], value: Any, schema: "Schema") -> Any:
    """Applies a field's custom operation directives in order (§7), each receiving the previous
    one's output -- `@skip`/`@include` never appear here, since `lower_query` already applied them
    structurally rather than carrying them through to execution.
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
        result = apply_directive(directive_function, value, mapped_arguments)
        if inspect.isawaitable(result):
            result = await result
        value = result
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
    if isinstance(value, (datetime.datetime, datetime.date)):
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
            lowered_field=lowered_field,
            selections=selections,
            path=path,
            state=state,
        )

    if raw_value is None:
        return None

    if type_info.kind == "LIST":
        results = []
        for index, item in enumerate(raw_value):
            item_path = Path(key=index, prev=path)
            try:
                completed = await _complete_value(
                    type_info=type_info.of_type,
                    raw_value=item,
                    lowered_field=lowered_field,
                    selections=selections,
                    path=item_path,
                    state=state,
                )
            except _PropagateNull:
                if type_info.of_type.kind == "NON_NULL":
                    raise
                completed = None
            results.append(completed)
        return results

    type_name = type_info.name
    assert type_name is not None  # NAMED is the only remaining kind, and it always carries a name.

    if type_name in state.schema.types_by_name or type_name in state.schema.union_members_by_name:
        info = _build_info(field_name=lowered_field.field_name, path=path, selections=selections, state=state)
        concrete_type = _resolve_concrete_type(type_name, raw_value, state.schema, info)
        return await _execute_selection_set(
            selections=selections,
            concrete_type=concrete_type,
            parent_value=raw_value,
            path=path,
            state=state,
        )

    return _serialize_scalar(type_name, raw_value, state.schema)


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
    info = _build_info(field_name=lowered_field.field_name, path=path, selections=selections, state=state)

    try:
        raw_value = await _resolve_field_value(field_info, lowered_field, parent_value, info, concrete_type, state.schema)
        raw_value = await _apply_custom_directives(lowered_field.directives, raw_value, state.schema)
    except _PropagateNull:
        raise
    except Exception as error:  # noqa: BLE001 -- deliberately broad: any resolver/directive failure becomes a field error, per §8.
        state.errors.append(_error_from_exception(error, path, lowered_field))
        if is_non_null:
            raise _PropagateNull from error
        return None

    try:
        return await _complete_value(
            type_info=field_info.type_info,
            raw_value=raw_value,
            lowered_field=lowered_field,
            selections=selections,
            path=path,
            state=state,
        )
    except _PropagateNull:
        if is_non_null:
            raise
        return None


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
) -> dict[str, Any]:
    """Executes one selection set against a known concrete type + resolved parent value (§8's
    per-field algorithm), after merging any same-response-key occurrences (`_group_by_response_key`).
    Fields run sequentially, not concurrently -- always correct for a mutation's root selection set
    (which the spec requires to be serial anyway), and a valid (if not maximally parallel) choice
    for queries too; parallelizing is a possible future optimization, not a correctness requirement.
    Raises `_PropagateNull` if the *entire* selection set must be discarded, i.e. one of its own
    non-null fields propagated up to here.
    """
    grouped = _group_by_response_key(_applicable_selections(selections, concrete_type, state.schema))

    result: dict[str, Any] = {}
    for response_key, occurrences in grouped.items():
        primary = occurrences[0]

        if primary.field_name == "__typename":
            result[response_key] = concrete_type.__bramble_type_info__.name
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
        field_path = Path(key=response_key, prev=path)
        result[response_key] = await _execute_field(
            field_info=field_info,
            lowered_field=primary,
            selections=merged_selections,
            parent_value=parent_value,
            concrete_type=concrete_type,
            path=field_path,
            state=state,
        )

    return result


def _error_to_dict(error: GraphQLError) -> dict[str, Any]:
    result: dict[str, Any] = {"message": error.message}
    if error.locations:
        result["locations"] = [{"line": line, "column": column} for line, column in error.locations]
    if error.path is not None:
        result["path"] = error.path
    result["extensions"] = {"code": error.code.value, **error.extensions}
    return result


_ROOT_TYPE_ATTRIBUTE_BY_OPERATION = {
    "query": "query",
    "mutation": "mutation",
    "subscription": "subscription",
}


async def execute_async(
    schema: "Schema",
    query: str,
    *,
    variable_values: dict[str, Any] | None = None,
    context: Any = None,
    root_value: Any = None,
    operation_name: str | None = None,
) -> dict[str, Any]:
    """Executes `query` against `schema` (§7a/§8/§11): validates and lowers it, then walks the
    result with full null-bubbling. A malformed query, a schema-shape validation failure, or an
    unknown operation all raise `bramble.GraphQLError` directly -- consistent with
    `Schema.validate_query`/`resolve_persisted_query`'s own behavior, these are request-level
    failures, not partial-response ones. Once execution actually starts, a resolver/completion
    failure instead becomes an entry in the returned `errors` list, per spec.
    """
    resolved_variable_values = variable_values or {}

    validate_query(query, schema._compiled, operation_name)
    operation_type, fields = lower_query(query, variable_values=resolved_variable_values, operation_name=operation_name)

    root_type = getattr(schema, _ROOT_TYPE_ATTRIBUTE_BY_OPERATION[operation_type])
    if root_type is None:
        raise GraphQLError(f"schema has no {operation_type} type", code=ErrorCode.GRAPHQL_VALIDATION_FAILED)

    if context is None and schema.execution_context_class is not None:
        context = schema.execution_context_class()

    errors: list[GraphQLError] = []
    state = _ExecutionState(
        schema=schema,
        context=context,
        root_value=root_value,
        variable_values=resolved_variable_values,
        query=query,
        errors=errors,
    )

    try:
        data: dict[str, Any] | None = await _execute_selection_set(
            selections=fields, concrete_type=root_type, parent_value=root_value, path=None, state=state
        )
    except _PropagateNull:
        data = None

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
        )
    )
