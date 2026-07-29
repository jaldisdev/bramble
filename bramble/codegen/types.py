from __future__ import annotations

import dataclasses


@dataclasses.dataclass(frozen=True)
class NamedType:
    """A bare reference by GraphQL name. Deliberately doesn't distinguish "built-in scalar" from
    "custom scalar" from "generated object/input type" here -- each output plugin owns that
    mapping itself (a Python plugin maps `"String"` -> `str`; a TypeScript one maps it ->
    `string`; anything not in a plugin's own built-in table is assumed to be one of the
    `Operation`'s own generated nested/input types, referenced by its exact `name`).
    """

    name: str


@dataclasses.dataclass(frozen=True)
class ListType:
    of_type: "CodegenType"


@dataclasses.dataclass(frozen=True)
class OptionalType:
    """Nullable. The *absence* of this wrapper means non-null -- matches
    `bramble_core::schema::GraphQLType`'s own convention (`NonNull` is the wrapper that exists;
    nullable is the unwrapped default), just inverted here since codegen's own types skew nullable
    by default the way Python/TypeScript's own type systems do.
    """

    of_type: "CodegenType"


CodegenType = NamedType | ListType | OptionalType


@dataclasses.dataclass(frozen=True)
class ObjectField:
    name: str
    type: CodegenType


@dataclasses.dataclass(frozen=True)
class ObjectType:
    """One generated shape -- either a query result's own (possibly deeply nested) object shape,
    or an input type reachable from one of the operation's own variables. Both are structurally
    identical (a name plus a flat list of named/typed fields), so they share this one IR node.
    """

    name: str
    fields: tuple[ObjectField, ...]


@dataclasses.dataclass(frozen=True)
class VariableDefinition:
    name: str
    type: CodegenType


@dataclasses.dataclass(frozen=True)
class Operation:
    name: str
    operation_type: str
    variables: tuple[VariableDefinition, ...]
    result_type: ObjectType
    # Every other named shape reached while walking the operation (nested result-field objects,
    # and any input types reachable from `variables`) -- order is insertion order (first
    # discovered), not sorted, but plugins with `from __future__ import annotations`-style
    # deferred type resolution don't need any particular order to still generate valid code.
    nested_types: tuple[ObjectType, ...]
