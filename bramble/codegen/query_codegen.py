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

from typing import TYPE_CHECKING, Any

from bramble._bramble import parse_query_document
from bramble._execution import _effective_name
from bramble.codegen.types import ListType, NamedType, ObjectField, ObjectType, Operation, OptionalType, VariableDefinition

if TYPE_CHECKING:
    from bramble._schema import Schema


class QueryCodegenError(Exception):
    """A `.graphql` query file couldn't be turned into an `Operation` -- an unparseable/ambiguous
    query, an unnamed operation, or a selection that doesn't resolve against the given schema.
    Distinct from `bramble.GraphQLError`: this is a codegen-time (offline, no request in flight)
    failure, not a request-time one.
    """


_ROOT_TYPE_ATTRIBUTE_BY_OPERATION = {"query": "query", "mutation": "mutation", "subscription": "subscription"}


def _effective_key(field_info: Any, schema: "Schema") -> str:
    return _effective_name(field_info.name, field_info.graphql_name, auto_camel_case=schema.config.auto_camel_case)


def _capitalize(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


def _flatten_selections(selections: list[Any], document: Any) -> list[Any]:
    """Expands fragment spreads and inline fragments into their own selections, recursively --
    unlike `bramble._execution`'s own field flattening, this deliberately does **not** track each
    result's `type_condition` (no interface/union-aware type-conditional codegen yet, a known,
    flagged gap -- see the module's own top-level note in `bramble/codegen/__init__.py`); a field
    scoped to `... on SomeType { ... }` is treated exactly like an unconditional one.
    """
    flattened = []
    for selection in selections:
        if selection.kind == "field":
            flattened.append(selection)
        elif selection.kind == "fragment_spread":
            fragment = next((f for f in document.fragments if f.name == selection.fragment_name), None)
            if fragment is None:
                raise QueryCodegenError(f"undefined fragment '{selection.fragment_name}'")
            flattened.extend(_flatten_selections(fragment.selections, document))
        elif selection.kind == "inline_fragment":
            flattened.extend(_flatten_selections(selection.selections, document))
    return flattened


def _register_input_object(type_name: str, schema: "Schema", object_registry: dict[str, ObjectType]) -> None:
    if type_name in object_registry:
        return
    # A placeholder breaks infinite recursion for a self-referential input type -- replaced with
    # the real fields once they're all resolved, a few lines down.
    object_registry[type_name] = ObjectType(type_name, ())

    input_type = schema.types_by_name[type_name]
    fields = tuple(
        ObjectField(
            _effective_key(field_info, schema),
            _codegen_type_from_type_info(field_info.type_info, selections=None, document=None, schema=schema, path=(), operation_name="", object_registry=object_registry),
        )
        for field_info in input_type.__bramble_type_info__.fields
    )
    object_registry[type_name] = ObjectType(type_name, fields)


def _build_object_type(
    type_name: str,
    selections: list[Any],
    *,
    document: Any,
    schema: "Schema",
    path: tuple[str, ...],
    operation_name: str,
    object_registry: dict[str, ObjectType],
) -> ObjectType:
    synthesized_name = operation_name + "".join(_capitalize(part) for part in path)

    concrete_type = schema.types_by_name.get(type_name)
    fields_by_name = {}
    if concrete_type is not None:
        for field_info in concrete_type.__bramble_type_info__.fields:
            fields_by_name[field_info.name] = field_info

    object_fields = []
    for selection in _flatten_selections(selections, document):
        response_key = selection.alias or selection.field_name

        if selection.field_name == "__typename":
            object_fields.append(ObjectField(response_key, NamedType("String")))
            continue

        field_info = _find_field_info(fields_by_name, selection.field_name, schema)
        if field_info is None:
            raise QueryCodegenError(f"field '{selection.field_name}' does not exist on type '{type_name}'")

        nested_path = (*path, response_key)
        field_type = _codegen_type_from_type_info(
            field_info.type_info,
            selections=selection.selections,
            document=document,
            schema=schema,
            path=nested_path,
            operation_name=operation_name,
            object_registry=object_registry,
        )
        object_fields.append(ObjectField(response_key, field_type))

    result = ObjectType(synthesized_name, tuple(object_fields))
    object_registry[synthesized_name] = result
    return result


def _find_field_info(fields_by_name: dict[str, Any], field_name: str, schema: "Schema") -> Any | None:
    for field_info in fields_by_name.values():
        if _effective_key(field_info, schema) == field_name:
            return field_info
    return None


def _codegen_type_from_type_info(
    type_info: Any,
    *,
    selections: list[Any] | None,
    document: Any,
    schema: "Schema",
    path: tuple[str, ...],
    operation_name: str,
    object_registry: dict[str, ObjectType],
) -> NamedType | ListType | OptionalType:
    if type_info.kind == "NON_NULL":
        return _codegen_named_or_list(
            type_info.of_type,
            selections=selections,
            document=document,
            schema=schema,
            path=path,
            operation_name=operation_name,
            object_registry=object_registry,
        )
    return OptionalType(
        _codegen_named_or_list(
            type_info,
            selections=selections,
            document=document,
            schema=schema,
            path=path,
            operation_name=operation_name,
            object_registry=object_registry,
        )
    )


def _codegen_named_or_list(
    type_info: Any,
    *,
    selections: list[Any] | None,
    document: Any,
    schema: "Schema",
    path: tuple[str, ...],
    operation_name: str,
    object_registry: dict[str, ObjectType],
) -> NamedType | ListType:
    if type_info.kind == "LIST":
        return ListType(
            _codegen_type_from_type_info(
                type_info.of_type,
                selections=selections,
                document=document,
                schema=schema,
                path=path,
                operation_name=operation_name,
                object_registry=object_registry,
            )
        )

    type_name = type_info.name
    schema_type = schema.types_by_name.get(type_name)

    if schema_type is not None and schema_type.__bramble_type_info__.kind == "input":
        _register_input_object(type_name, schema, object_registry)
        return NamedType(type_name)

    if selections is not None and (type_name in schema.types_by_name or type_name in schema.union_members_by_name):
        nested = _build_object_type(
            type_name,
            selections,
            document=document,
            schema=schema,
            path=path,
            operation_name=operation_name,
            object_registry=object_registry,
        )
        return NamedType(nested.name)

    return NamedType(type_name)


def _codegen_type_for_variable(type_str: str, schema: "Schema", object_registry: dict[str, ObjectType]) -> Any:
    if type_str.endswith("!"):
        return _codegen_variable_core(type_str[:-1], schema, object_registry)
    return OptionalType(_codegen_variable_core(type_str, schema, object_registry))


def _codegen_variable_core(type_str: str, schema: "Schema", object_registry: dict[str, ObjectType]) -> Any:
    if type_str.startswith("[") and type_str.endswith("]"):
        return ListType(_codegen_type_for_variable(type_str[1:-1], schema, object_registry))

    schema_type = schema.types_by_name.get(type_str)
    if schema_type is not None and schema_type.__bramble_type_info__.kind == "input":
        _register_input_object(type_str, schema, object_registry)
    return NamedType(type_str)


def generate_operation(schema: "Schema", query_text: str) -> Operation:
    """Parses `query_text` (expected to contain exactly one, named operation -- plus any fragments
    it spreads) and resolves its selection set and variable declarations against `schema`, building
    an `Operation` an output plugin (see `bramble.codegen.plugins`) can render into real code.
    """
    document = parse_query_document(query_text)

    if len(document.operations) != 1:
        raise QueryCodegenError("a codegen query file must contain exactly one operation")
    operation = document.operations[0]
    if operation.name is None:
        raise QueryCodegenError("codegen requires every operation to be named")

    root_class = getattr(schema, _ROOT_TYPE_ATTRIBUTE_BY_OPERATION[operation.operation_type])
    if root_class is None:
        raise QueryCodegenError(f"schema has no {operation.operation_type} type")
    root_type_name = root_class.__bramble_type_info__.name

    object_registry: dict[str, ObjectType] = {}
    result_type = _build_object_type(
        root_type_name,
        operation.selections,
        document=document,
        schema=schema,
        path=("Result",),
        operation_name=operation.name,
        object_registry=object_registry,
    )
    # `_build_object_type` registers its own result into `object_registry` too (every nested
    # object type does, uniformly) -- but the top-level result has its own dedicated field on
    # `Operation`, so it's excluded from `nested_types` to avoid emitting it twice.
    object_registry.pop(result_type.name, None)

    variables = tuple(
        VariableDefinition(name=variable.name, type=_codegen_type_for_variable(variable.type_str, schema, object_registry))
        for variable in operation.variables
    )

    return Operation(
        name=operation.name,
        operation_type=operation.operation_type,
        variables=variables,
        result_type=result_type,
        nested_types=tuple(object_registry.values()),
    )
