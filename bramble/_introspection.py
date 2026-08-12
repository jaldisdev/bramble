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

"""GraphQL introspection (§ 4.5): the `__schema`/`__type` meta-fields and the `__Schema`/`__Type`/
`__Field`/`__InputValue`/`__EnumValue`/`__Directive` types behind them.

Built as ordinary `@bramble.type` classes rather than special-cased anywhere in the executor or
validator, exactly the way `bramble.federation` synthesizes `_service`/`_entities` -- registering
them as real types means query validation, field resolution, null-bubbling and SDL rendering all
work through the paths that already exist, with no parallel implementation to keep in step.

Each class is *named* `__Type`/`__Field`/... in Python, not given a `name=` override, deliberately:
a self-referential annotation (`__Type.ofType -> __Type`) is resolved while that very class is
still being decorated, so its `__bramble_type_info__` doesn't exist yet and the resolver falls back
to the Python `__name__`. Matching the two names keeps every self-reference correct.

Resolvers read from `_TypeRef`/`FieldInfo`/... values rather than from the introspection classes
themselves -- the same "domain object separate from the GraphQL type" split `examples/blog` uses,
and what lets one `__Type` describe an object, a scalar, an enum, and a `[X!]!` wrapper alike.
"""

from __future__ import annotations

import dataclasses
import enum as enum_module
from typing import Any

# Imported from the private modules rather than the `bramble` package facade: `bramble/__init__`
# imports `bramble._schema`, which imports this module, so the facade's own names aren't bound yet
# while this file executes.
from bramble._enum import enum as bramble_enum
from bramble._resolver import Info, Parent
from bramble._type import field as bramble_field
from bramble._type import type as bramble_type

# The scalars a schema always has available even when nothing registered them explicitly -- a
# field typed `str`/`int`/... resolves to one of these names, so introspection has to be able to
# describe them or a client walking `__schema.types` finds a dangling reference.
_BUILTIN_SCALAR_NAMES = ("String", "Int", "Float", "Boolean", "ID")


# Python identifier deliberately single-underscored while the GraphQL name keeps both: inside a
# class body (`class __Type:`) Python mangles any `__name` reference into `_Type__name`, so a
# double-underscored Python name here would resolve fine in annotations (plain strings) yet blow up
# with `NameError: _Type__TypeKind` the moment a resolver *body* touched it.
@bramble_enum(name="__TypeKind")
class _TypeKind(enum_module.Enum):
    SCALAR = "SCALAR"
    OBJECT = "OBJECT"
    INTERFACE = "INTERFACE"
    UNION = "UNION"
    ENUM = "ENUM"
    INPUT_OBJECT = "INPUT_OBJECT"
    LIST = "LIST"
    NON_NULL = "NON_NULL"


@dataclasses.dataclass(frozen=True)
class _TypeRef:
    """What a `__Type` actually resolves from: either a named type in the schema, or a
    `LIST`/`NON_NULL` wrapper around another reference. Mirrors `GraphQLTypeInfo`'s own shape,
    but with the *named* kind already resolved (`OBJECT` vs `SCALAR` vs `ENUM` ...), which
    `GraphQLTypeInfo` doesn't carry -- it only knows a name.
    """

    kind: _TypeKind
    name: str | None = None
    of_type: "_TypeRef | None" = None


def _kind_of_named_type(name: str, schema: Any) -> _TypeKind:
    type_class = schema.types_by_name.get(name)
    if type_class is not None:
        kind = type_class.__bramble_type_info__.kind
        return {
            "type": _TypeKind.OBJECT,
            "interface": _TypeKind.INTERFACE,
            "input": _TypeKind.INPUT_OBJECT,
            "enum": _TypeKind.ENUM,
        }[kind]
    if name in schema.unions_by_name:
        return _TypeKind.UNION
    # Anything else a field can name is a scalar -- registered via `scalar_map`, a built-in, or an
    # unregistered custom scalar (which still executes correctly; see `docs/types/scalars.md`).
    return _TypeKind.SCALAR


def _ref_from_type_info(type_info: Any, schema: Any) -> _TypeRef:
    """Converts a `GraphQLTypeInfo` (bramble's own field/argument type structure) into the
    `_TypeRef` introspection describes, resolving each named type's real kind along the way.
    """
    if type_info.kind == "NON_NULL":
        return _TypeRef(kind=_TypeKind.NON_NULL, of_type=_ref_from_type_info(type_info.of_type, schema))
    if type_info.kind == "LIST":
        return _TypeRef(kind=_TypeKind.LIST, of_type=_ref_from_type_info(type_info.of_type, schema))
    return _TypeRef(kind=_kind_of_named_type(type_info.name, schema), name=type_info.name)


def _named_ref(name: str, schema: Any) -> _TypeRef:
    return _TypeRef(kind=_kind_of_named_type(name, schema), name=name)


def _effective_name(info: Any, schema: Any) -> str:
    """A field/argument's GraphQL-facing name -- its `name=` override, else camelCase or the raw
    identifier depending on `SchemaConfig.auto_camel_case`. Reuses the executor's own helper so
    introspection reports exactly the names a query must actually use.
    """
    from bramble._execution import _effective_name as effective_name

    return effective_name(info.name, info.graphql_name, auto_camel_case=schema.config.auto_camel_case)


@dataclasses.dataclass(frozen=True)
class _InputValueRef:
    """One `__InputValue`: a field/directive argument, or an input object's own field. Both are
    described by the same introspection type, but bramble represents them with different objects
    (`ArgumentInfo` vs `FieldInfo`), so this normalizes the parts introspection needs.
    """

    name: str
    description: str | None
    type_ref: _TypeRef
    deprecation_reason: str | None = None
    # The default as a GraphQL literal string (`"10"`, `'"abc"'`, `"RED"`), which is exactly what
    # `__InputValue.defaultValue` is defined to return -- rendered once in Rust so SDL and
    # introspection can't drift apart. `None` means no default.
    default_value: str | None = None


@dataclasses.dataclass(frozen=True)
class _DirectiveRef:
    name: str
    description: str | None
    locations: list[str]
    args: list[_InputValueRef]
    is_repeatable: bool


class __EnumValue:
    @bramble_field
    def name(parent: Parent[Any]) -> str:
        return parent.graphql_name or parent.name

    @bramble_field
    def description(parent: Parent[Any]) -> str | None:
        return parent.description

    @bramble_field
    def is_deprecated(parent: Parent[Any]) -> bool:
        return parent.deprecation_reason is not None

    @bramble_field
    def deprecation_reason(parent: Parent[Any]) -> str | None:
        return parent.deprecation_reason


class __InputValue:
    @bramble_field
    def name(parent: Parent[_InputValueRef]) -> str:
        return parent.name

    @bramble_field
    def description(parent: Parent[_InputValueRef]) -> str | None:
        return parent.description

    @bramble_field
    def type(parent: Parent[_InputValueRef]) -> "__Type":
        return parent.type_ref

    @bramble_field
    def default_value(parent: Parent[_InputValueRef]) -> str | None:
        """The default as a GraphQL literal string, per the spec's definition of
        `__InputValue.defaultValue` -- `null` when there is no default, or when the default has no
        faithful literal spelling (an arbitrary Python object), in which case rendering nothing is
        better than rendering something wrong.
        """
        return parent.default_value

    @bramble_field
    def is_deprecated(parent: Parent[_InputValueRef]) -> bool:
        return parent.deprecation_reason is not None

    @bramble_field
    def deprecation_reason(parent: Parent[_InputValueRef]) -> str | None:
        return parent.deprecation_reason


class __Field:
    @bramble_field
    def name(parent: Parent[Any], info: Info) -> str:
        return _effective_name(parent, info.schema)

    @bramble_field
    def description(parent: Parent[Any]) -> str | None:
        return parent.description

    @bramble_field
    def args(parent: Parent[Any], info: Info, include_deprecated: bool = False) -> list["__InputValue"]:
        return [
            _InputValueRef(
                name=_effective_name(argument, info.schema),
                description=argument.description,
                type_ref=_ref_from_type_info(argument.type_info, info.schema),
                deprecation_reason=argument.deprecation_reason,
                default_value=argument.default_value,
            )
            for argument in parent.arguments
            if include_deprecated or argument.deprecation_reason is None
        ]

    @bramble_field
    def type(parent: Parent[Any], info: Info) -> "__Type":
        return _ref_from_type_info(parent.type_info, info.schema)

    @bramble_field
    def is_deprecated(parent: Parent[Any]) -> bool:
        """Always `false`: bramble has no field-level deprecation API (`bramble.field(...)` takes
        no `deprecation_reason`), unlike arguments and enum values which do.
        """
        return False

    @bramble_field
    def deprecation_reason(parent: Parent[Any]) -> str | None:
        return None


class __Directive:
    @bramble_field
    def name(parent: Parent[_DirectiveRef]) -> str:
        return parent.name

    @bramble_field
    def description(parent: Parent[_DirectiveRef]) -> str | None:
        return parent.description

    @bramble_field
    def locations(parent: Parent[_DirectiveRef]) -> list[str]:
        return parent.locations

    @bramble_field
    def args(parent: Parent[_DirectiveRef], include_deprecated: bool = False) -> list["__InputValue"]:
        return [arg for arg in parent.args if include_deprecated or arg.deprecation_reason is None]

    @bramble_field
    def is_repeatable(parent: Parent[_DirectiveRef]) -> bool:
        return parent.is_repeatable


class __Type:
    @bramble_field
    def kind(parent: Parent[_TypeRef]) -> _TypeKind:
        return parent.kind

    @bramble_field
    def name(parent: Parent[_TypeRef]) -> str | None:
        return parent.name

    @bramble_field
    def description(parent: Parent[_TypeRef], info: Info) -> str | None:
        if parent.name is None:
            return None
        type_class = info.schema.types_by_name.get(parent.name)
        if type_class is not None:
            return type_class.__bramble_type_info__.description
        scalar = info.schema.scalars_by_name.get(parent.name)
        return scalar.description if scalar is not None else None

    @bramble_field
    def fields(parent: Parent[_TypeRef], info: Info, include_deprecated: bool = False) -> list["__Field"] | None:
        if parent.kind not in (_TypeKind.OBJECT, _TypeKind.INTERFACE) or parent.name is None:
            return None
        type_class = info.schema.types_by_name.get(parent.name)
        if type_class is None:
            return None
        return list(type_class.__bramble_type_info__.fields)

    @bramble_field
    def interfaces(parent: Parent[_TypeRef], info: Info) -> list["__Type"] | None:
        if parent.kind is not _TypeKind.OBJECT or parent.name is None:
            return None
        type_class = info.schema.types_by_name.get(parent.name)
        if type_class is None:
            return []
        # Read off the MRO rather than the Rust IR: `TypeInfo` doesn't expose its `interfaces`
        # list to Python, and inheritance is how bramble models "implements" in the first place.
        return [
            _named_ref(base.__bramble_type_info__.name, info.schema)
            for base in type_class.__mro__[1:]
            if getattr(base, "__bramble_type_info__", None) is not None
            and base.__bramble_type_info__.kind == "interface"
        ]

    @bramble_field
    def possible_types(parent: Parent[_TypeRef], info: Info) -> list["__Type"] | None:
        if parent.name is None:
            return None
        if parent.kind is _TypeKind.INTERFACE:
            return [
                _named_ref(implementor.__bramble_type_info__.name, info.schema)
                for implementor in info.schema.implementors_by_interface.get(parent.name, [])
            ]
        if parent.kind is _TypeKind.UNION:
            return [
                _named_ref(member.__bramble_type_info__.name, info.schema)
                for member in info.schema.union_members_by_name.get(parent.name, [])
            ]
        return None

    @bramble_field
    def enum_values(
        parent: Parent[_TypeRef], info: Info, include_deprecated: bool = False
    ) -> list["__EnumValue"] | None:
        if parent.kind is not _TypeKind.ENUM or parent.name is None:
            return None
        type_class = info.schema.types_by_name.get(parent.name)
        if type_class is None:
            return None
        return [
            value
            for value in type_class.__bramble_type_info__.enum_values
            if include_deprecated or value.deprecation_reason is None
        ]

    @bramble_field
    def input_fields(
        parent: Parent[_TypeRef], info: Info, include_deprecated: bool = False
    ) -> list["__InputValue"] | None:
        if parent.kind is not _TypeKind.INPUT_OBJECT or parent.name is None:
            return None
        type_class = info.schema.types_by_name.get(parent.name)
        if type_class is None:
            return None
        return [
            _InputValueRef(
                name=_effective_name(field_info, info.schema),
                description=field_info.description,
                type_ref=_ref_from_type_info(field_info.type_info, info.schema),
            )
            for field_info in type_class.__bramble_type_info__.fields
        ]

    @bramble_field
    def of_type(parent: Parent[_TypeRef]) -> "__Type | None":
        return parent.of_type

    @bramble_field
    def specified_by_url(parent: Parent[_TypeRef], info: Info) -> str | None:
        if parent.name is None:
            return None
        scalar = info.schema.scalars_by_name.get(parent.name)
        return scalar.specified_by_url if scalar is not None else None


def _all_type_refs(schema: Any) -> list[_TypeRef]:
    """Every named type in the schema, introspection-shaped: the declared object/interface/input/
    enum types, every union, every registered scalar, and the built-in scalars (which are never
    "registered" anywhere but are always referencable).
    """
    refs = [_named_ref(name, schema) for name in schema.types_by_name]
    refs += [_TypeRef(kind=_TypeKind.UNION, name=name) for name in schema.unions_by_name]
    scalar_names = list(schema.scalars_by_name) + [
        name for name in _BUILTIN_SCALAR_NAMES if name not in schema.scalars_by_name
    ]
    refs += [_TypeRef(kind=_TypeKind.SCALAR, name=name) for name in scalar_names]
    return refs


def _all_directive_refs(schema: Any) -> list[_DirectiveRef]:
    """Both directive flavours a client can see: custom *operation* directives (usable inside a
    query document) and *schema* directives (type-system metadata). bramble's built-in
    `@skip`/`@include` are applied structurally during lowering rather than being registered
    anywhere, so they have no definition to report here.
    """
    directives: list[_DirectiveRef] = []

    for directive_function in schema.directive_functions_by_name.values():
        directive_info = directive_function.__bramble_directive_info__
        directives.append(
            _DirectiveRef(
                name=directive_info.name,
                description=directive_info.description,
                locations=list(directive_info.locations),
                args=[
                    _InputValueRef(
                        name=_effective_name(argument, schema),
                        description=argument.description,
                        type_ref=_ref_from_type_info(argument.type_info, schema),
                        deprecation_reason=argument.deprecation_reason,
                        default_value=argument.default_value,
                    )
                    for argument in directive_info.arguments
                ],
                is_repeatable=False,
            )
        )

    for directive_info in schema.schema_directives_by_name.values():
        directives.append(
            _DirectiveRef(
                name=directive_info.name,
                description=directive_info.description,
                locations=list(directive_info.locations),
                # A schema directive's own fields are `DirectiveFieldInfo`, which carries no
                # type_info -- reported without argument types rather than guessing at one.
                args=[],
                is_repeatable=directive_info.repeatable,
            )
        )

    return directives


@dataclasses.dataclass(frozen=True)
class _SchemaRef:
    """Marker parent value for `__schema` -- everything it needs comes from `Info.schema`, so this
    carries nothing itself; it exists only because a field has to resolve to *something*.
    """


class __Schema:
    @bramble_field
    def description(parent: Parent[_SchemaRef]) -> str | None:
        return None

    @bramble_field
    def types(parent: Parent[_SchemaRef], info: Info) -> list["__Type"]:
        return _all_type_refs(info.schema)

    @bramble_field
    def query_type(parent: Parent[_SchemaRef], info: Info) -> "__Type":
        return _named_ref(info.schema.query.__bramble_type_info__.name, info.schema)

    @bramble_field
    def mutation_type(parent: Parent[_SchemaRef], info: Info) -> "__Type | None":
        mutation = info.schema.mutation
        return None if mutation is None else _named_ref(mutation.__bramble_type_info__.name, info.schema)

    @bramble_field
    def subscription_type(parent: Parent[_SchemaRef], info: Info) -> "__Type | None":
        subscription = info.schema.subscription
        return None if subscription is None else _named_ref(subscription.__bramble_type_info__.name, info.schema)

    @bramble_field
    def directives(parent: Parent[_SchemaRef], info: Info) -> list["__Directive"]:
        return _all_directive_refs(info.schema)


# Decorated here rather than inline, because these six types are mutually recursive
# (`__Type.fields -> [__Field]`, `__Field.type -> __Type`, ...) and a *resolver's* annotation --
# unlike a plain field's -- is a hard error if it names a class that doesn't exist yet. Defining
# every class first, then decorating, means each name is already bound by the time any annotation
# is resolved, whichever order they're processed in.
__EnumValue = bramble_type(__EnumValue)
__InputValue = bramble_type(__InputValue)
__Field = bramble_type(__Field)
__Directive = bramble_type(__Directive)
__Type = bramble_type(__Type)
__Schema = bramble_type(__Schema)


def resolve_schema_field(info: Info) -> __Schema:
    return _SchemaRef()


def resolve_type_field(name: str, info: Info) -> __Type | None:
    known = (
        name in info.schema.types_by_name
        or name in info.schema.unions_by_name
        or name in info.schema.scalars_by_name
        or name in _BUILTIN_SCALAR_NAMES
    )
    return _named_ref(name, info.schema) if known else None


#: Every type introspection itself contributes to the schema. Passed to `Schema(types=[...])`'s
#: own discovery so they are all registered even though only `__Schema`/`__Type` are named by the
#: injected root fields -- `__Field`/`__InputValue`/`__EnumValue`/`__Directive`/`_TypeKind` are
#: reachable only through resolver return annotations that discovery does follow, but listing them
#: explicitly keeps that independent of how the walker happens to traverse.
INTROSPECTION_TYPES = (__Schema, __Type, __Field, __InputValue, __EnumValue, __Directive, _TypeKind)

__all__ = ["INTROSPECTION_TYPES", "resolve_schema_field", "resolve_type_field"]
