from __future__ import annotations

import abc

from bramble.codegen.types import CodegenType, ListType, NamedType, Operation, OptionalType


class QueryCodegenPlugin(abc.ABC):
    """One output language/format for `generate_operation`'s own `Operation` IR. bramble ships
    `PythonPlugin`/`TypeScriptPlugin`; a project-specific plugin just needs to implement
    `generate_code` (and, optionally, override `file_extension`) -- `bramble codegen -p
    some_module:SomePlugin` loads it the same way it loads a builtin one.
    """

    file_extension: str = "txt"

    @abc.abstractmethod
    def generate_code(self, operation: Operation) -> str: ...


_PYTHON_SCALAR_MAP: dict[str, str] = {
    "String": "str",
    "Int": "int",
    "Float": "float",
    "Boolean": "bool",
    "ID": "str",
    "DateTime": "datetime.datetime",
    "Date": "datetime.date",
    "Time": "datetime.time",
    "Decimal": "decimal.Decimal",
    "UUID": "uuid.UUID",
}

_TYPESCRIPT_SCALAR_MAP: dict[str, str] = {
    "String": "string",
    "Int": "number",
    "Float": "number",
    "Boolean": "boolean",
    "ID": "string",
    "DateTime": "string",
    "Date": "string",
    "Time": "string",
    "Decimal": "string",
    "UUID": "string",
}


def _type_str(type_: CodegenType, scalar_map: dict[str, str], known_names: set[str], *, optional_suffix: str, list_format: str) -> str:
    if isinstance(type_, OptionalType):
        return f"{_type_str(type_.of_type, scalar_map, known_names, optional_suffix=optional_suffix, list_format=list_format)}{optional_suffix}"
    if isinstance(type_, ListType):
        inner = _type_str(type_.of_type, scalar_map, known_names, optional_suffix=optional_suffix, list_format=list_format)
        return list_format.format(inner)
    if type_.name in known_names:
        return type_.name
    return scalar_map.get(type_.name, type_.name)


def _known_names(operation: Operation) -> set[str]:
    return {operation.result_type.name, *(nested.name for nested in operation.nested_types)}


class PythonPlugin(QueryCodegenPlugin):
    """Generates one `@dataclasses.dataclass` per shape (every nested result-field object, every
    input type reachable from a variable, and the operation's own top-level result), plus a
    `{OperationName}Variables` dataclass for its input variables. `from __future__ import
    annotations` is emitted so field annotations can forward-reference a not-yet-defined sibling
    dataclass freely -- generation order doesn't need to matter.
    """

    file_extension = "py"

    def generate_code(self, operation: Operation) -> str:
        known_names = _known_names(operation)
        lines = ["from __future__ import annotations", "", "import dataclasses", ""]

        for shape in (*operation.nested_types, operation.result_type):
            lines.append("@dataclasses.dataclass")
            lines.append(f"class {shape.name}:")
            if not shape.fields:
                lines.append("    pass")
            for field in shape.fields:
                type_str = _type_str(field.type, _PYTHON_SCALAR_MAP, known_names, optional_suffix=" | None", list_format="list[{}]")
                lines.append(f"    {field.name}: {type_str}")
            lines.append("")
            lines.append("")

        if operation.variables:
            lines.append("@dataclasses.dataclass")
            lines.append(f"class {operation.name}Variables:")
            for variable in operation.variables:
                type_str = _type_str(variable.type, _PYTHON_SCALAR_MAP, known_names, optional_suffix=" | None", list_format="list[{}]")
                lines.append(f"    {variable.name}: {type_str}")
            lines.append("")

        return "\n".join(lines)


class TypeScriptPlugin(QueryCodegenPlugin):
    """Generates one `export type` per shape, the same set `PythonPlugin` does."""

    file_extension = "ts"

    def generate_code(self, operation: Operation) -> str:
        known_names = _known_names(operation)
        lines: list[str] = []

        for shape in (*operation.nested_types, operation.result_type):
            lines.append(f"export type {shape.name} = {{")
            for field in shape.fields:
                type_str = _type_str(field.type, _TYPESCRIPT_SCALAR_MAP, known_names, optional_suffix=" | null", list_format="Array<{}>")
                lines.append(f"  {field.name}: {type_str};")
            lines.append("};")
            lines.append("")

        if operation.variables:
            lines.append(f"export type {operation.name}Variables = {{")
            for variable in operation.variables:
                type_str = _type_str(variable.type, _TYPESCRIPT_SCALAR_MAP, known_names, optional_suffix=" | null", list_format="Array<{}>")
                lines.append(f"  {variable.name}: {type_str};")
            lines.append("};")
            lines.append("")

        return "\n".join(lines)


_PLUGINS_BY_NAME: dict[str, type[QueryCodegenPlugin]] = {
    "python": PythonPlugin,
    "typescript": TypeScriptPlugin,
}


def get_builtin_plugin(name: str) -> type[QueryCodegenPlugin] | None:
    return _PLUGINS_BY_NAME.get(name)
