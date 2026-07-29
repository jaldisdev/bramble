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
    lowered_field: "LoweredField",
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
    info.selected_fields = _selected_fields(lowered_field.selections)
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


def _inner_type_name(type_info: "GraphQLTypeInfo") -> str | None:
    node = type_info
    while node.name is None and node.of_type is not None:
        node = node.of_type
    return node.name


def _coerce_argument_value(argument_info: "ArgumentInfo", value: Any, schema: "Schema") -> Any:
    """Applies a registered custom scalar's `parse_value` hook (§3b) to a resolved argument value,
    if the argument's own named type matches one. Only checked at the argument's own top level --
    a custom scalar nested inside a list/input-object argument isn't coerced, a deliberate scope
    limit (recursing through arbitrary argument shapes is significantly more machinery for a case
    bramble's own test suite doesn't yet need).
    """
    if value is None:
        return value
    type_name = _inner_type_name(argument_info.type_info)
    scalar_definition = schema.scalars_by_name.get(type_name) if type_name is not None else None
    if scalar_definition is not None and scalar_definition.parse_value is not None:
        return scalar_definition.parse_value(value)
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
            kwargs[argument.name] = _coerce_argument_value(argument, kwargs[argument.name], schema)
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


def _build_error(message: str, path: Path) -> GraphQLError:
    # No `locations` here -- `LoweredField` doesn't carry the query's own source positions (only
    # `bramble-core`'s parse/validation errors do), so an execution-time error can only report
    # `path`. Flagged scope, not an oversight: threading `Pos` through the whole lowering pipeline
    # is more machinery than field-level error reporting has needed so far.
    return GraphQLError(message, code=ErrorCode.FIELD_RESOLUTION_FAILED, path=path.as_list())


def _error_from_exception(error: Exception, path: Path) -> GraphQLError:
    """A resolver that deliberately raises its own `bramble.GraphQLError` (a custom `code`,
    `extensions`, etc.) keeps all of that -- only `path` is overwritten, since the resolver has no
    way to know its own position in the response ahead of time. Any other exception is wrapped
    generically (`_build_error`), same as an unexpected bug in resolver code.
    """
    if isinstance(error, GraphQLError):
        error.path = path.as_list()
        return error
    return _build_error(str(error), path)


async def _complete_value(
    *,
    type_info: "GraphQLTypeInfo",
    raw_value: Any,
    lowered_field: "LoweredField",
    path: Path,
    state: _ExecutionState,
) -> Any:
    """Implements GraphQL's `CompleteValue` (§8/§11): unwraps `NonNull`/`List` recursively,
    recurses into a nested selection set for object/interface/union types (dispatching the
    concrete type first for the latter two), and serializes scalar leaves. Raises
    `_PropagateNull` when a non-null boundary is violated, after recording the responsible error --
    the caller (a list item's loop, or `_execute_field`) decides from there whether it must keep
    propagating (its own slot is also non-null) or can absorb the failure as `None`.
    """
    if type_info.kind == "NON_NULL":
        if raw_value is None:
            state.errors.append(_build_error("Cannot return null for non-nullable field.", path))
            raise _PropagateNull
        return await _complete_value(
            type_info=type_info.of_type, raw_value=raw_value, lowered_field=lowered_field, path=path, state=state
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
        info = _build_info(field_name=lowered_field.field_name, path=path, lowered_field=lowered_field, state=state)
        concrete_type = _resolve_concrete_type(type_name, raw_value, state.schema, info)
        return await _execute_selection_set(
            selections=lowered_field.selections,
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
    parent_value: Any,
    concrete_type: type,
    path: Path,
    state: _ExecutionState,
) -> Any:
    is_non_null = field_info.type_info.kind == "NON_NULL"
    info = _build_info(field_name=lowered_field.field_name, path=path, lowered_field=lowered_field, state=state)

    try:
        raw_value = await _resolve_field_value(field_info, lowered_field, parent_value, info, concrete_type, state.schema)
        raw_value = await _apply_custom_directives(lowered_field.directives, raw_value, state.schema)
    except _PropagateNull:
        raise
    except Exception as error:  # noqa: BLE001 -- deliberately broad: any resolver/directive failure becomes a field error, per §8.
        state.errors.append(_error_from_exception(error, path))
        if is_non_null:
            raise _PropagateNull from error
        return None

    try:
        return await _complete_value(type_info=field_info.type_info, raw_value=raw_value, lowered_field=lowered_field, path=path, state=state)
    except _PropagateNull:
        if is_non_null:
            raise
        return None


async def _execute_selection_set(
    *,
    selections: Sequence["LoweredField"],
    concrete_type: type,
    parent_value: Any,
    path: Path | None,
    state: _ExecutionState,
) -> dict[str, Any]:
    """Executes one selection set against a known concrete type + resolved parent value (§8's
    per-field algorithm). Fields run sequentially, not concurrently -- always correct for a
    mutation's root selection set (which the spec requires to be serial anyway), and a valid
    (if not maximally parallel) choice for queries too; parallelizing is a possible future
    optimization, not a correctness requirement. Raises `_PropagateNull` if the *entire* selection
    set must be discarded, i.e. one of its own non-null fields propagated up to here.
    """
    result: dict[str, Any] = {}
    for lowered_field in _applicable_selections(selections, concrete_type, state.schema):
        response_key = lowered_field.response_key

        if lowered_field.field_name == "__typename":
            result[response_key] = concrete_type.__bramble_type_info__.name
            continue

        field_info = _find_field_info(
            concrete_type, lowered_field.field_name, auto_camel_case=state.schema.config.auto_camel_case
        )
        if field_info is None:
            raise GraphQLError(
                f"field '{lowered_field.field_name}' does not exist on type "
                f"'{concrete_type.__bramble_type_info__.name}'",
                code=ErrorCode.UNKNOWN_FIELD,
            )

        field_path = Path(key=response_key, prev=path)
        result[response_key] = await _execute_field(
            field_info=field_info,
            lowered_field=lowered_field,
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
