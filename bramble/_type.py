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

import dataclasses
import inspect
import sys
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Literal

from bramble._bramble import SchemaError, process_type
from bramble.schema_directive import Location

_type = type  # capture the builtin before `type` (below) shadows this module's name for it

_LOCATION_BY_KIND: dict[str, Location] = {
    "type": Location.OBJECT,
    "interface": Location.INTERFACE,
    "input": Location.INPUT_OBJECT,
}


class Field(dataclasses.Field):
    """What `bramble.field(...)` produces: a real `dataclasses.Field` carrying bramble's extra
    GraphQL metadata, plus an optional resolver.

    Subclassing `dataclasses.Field` is load-bearing rather than stylistic -- `dataclasses.dataclass()`
    only treats a class attribute as a field descriptor (rather than a literal default value) when
    it passes `isinstance(attribute, dataclasses.Field)`. That is what lets a bramble type be an
    ordinary dataclass, with `dataclasses.fields()`, `__init__`, and `__eq__` all behaving normally.

    Rarely constructed directly; use `bramble.field(...)`.
    """

    def __init__(
        self,
        resolver: Callable[..., Any] | None = None,
        *,
        name: str | None = None,
        description: str | None = None,
        deprecation_reason: str | None = None,
        permission_classes: Sequence[_type] = (),
        directives: Sequence[object] = (),
        extensions: Sequence[object] = (),
        metadata: Mapping[Any, Any] | None = None,
        default: Any = dataclasses.MISSING,
        default_factory: Any = dataclasses.MISSING,
    ) -> None:
        if default is not dataclasses.MISSING and default_factory is not dataclasses.MISSING:
            raise SchemaError("a field cannot specify both 'default' and 'default_factory'")
        if resolver is not None and (default is not dataclasses.MISSING or default_factory is not dataclasses.MISSING):
            raise SchemaError("a field with a resolver cannot also declare a default value")
        # Field extensions have no hook point in the execution pipeline yet. The parameter exists so
        # the eventual API is already shaped, but accepting values for it silently would be worse
        # than rejecting them: a user porting a tracing/auth/caching extension would get a schema
        # that builds, runs, and quietly does none of what the extension was for.
        if extensions:
            raise SchemaError(
                "bramble.field(extensions=...) is not implemented yet -- field extensions have no "
                "execution hook point, so any value here would be silently ignored"
            )

        # A resolver-backed field is computed at execution time, not user-supplied, so it's
        # excluded from the generated __init__/__repr__/__eq__. This is also why Field needs to
        # subclass dataclasses.Field at all: dataclasses.dataclass() only recognizes a class
        # attribute as a field descriptor (as opposed to a literal default value) via
        # `isinstance(attribute, dataclasses.Field)`.
        is_basic_field = resolver is None

        kwargs: dict[str, Any] = {"kw_only": True}
        if sys.version_info >= (3, 14):
            kwargs["doc"] = None

        super().__init__(
            default=default,
            default_factory=default_factory,
            init=is_basic_field,
            repr=is_basic_field,
            compare=is_basic_field,
            hash=None,
            metadata=metadata,
            **kwargs,
        )

        # `dataclasses.Field.name` is reserved for the Python attribute name (assigned by
        # dataclasses itself while processing the class); the GraphQL name override lives
        # under a different attribute to avoid clobbering it.
        self.graphql_name = name
        self.description = description
        self.deprecation_reason = deprecation_reason
        self.permission_classes = tuple(permission_classes)
        self.directives = tuple(directives)
        self.extensions = tuple(extensions)
        self._resolver: Callable[..., Any] | None = None
        if resolver is not None:
            self.resolver = resolver

    @property
    def resolver(self) -> Callable[..., Any] | None:
        return self._resolver

    @resolver.setter
    def resolver(self, resolver: Callable[..., Any]) -> None:
        if self.default is not dataclasses.MISSING or self.default_factory is not dataclasses.MISSING:
            raise SchemaError("a field with a resolver cannot also declare a default value")
        self._resolver = resolver
        self.init = False
        self.repr = False
        self.compare = False

    def __call__(self, resolver: Callable[..., Any]) -> Field:
        self.resolver = resolver
        return self


def field(
    resolver: Callable[..., Any] | None = None,
    *,
    name: str | None = None,
    description: str | None = None,
    deprecation_reason: str | None = None,
    permission_classes: Sequence[_type] = (),
    directives: Sequence[object] = (),
    extensions: Sequence[object] = (),
    metadata: Mapping[Any, Any] | None = None,
    default: Any = dataclasses.MISSING,
    default_factory: Any = dataclasses.MISSING,
) -> Any:
    """Declares a field on a `@bramble.type`/`@bramble.interface`/`@bramble.input` class.

    Usable three ways:

        @bramble.type
        class Query:
            # 1. As a decorator on a resolver.
            @bramble.field
            def greet(name: str) -> str:
                return f"Hello, {name}!"

            # 2. As a decorator with configuration.
            @bramble.field(description="The current user", deprecation_reason="use viewer")
            def me() -> "User": ...

            # 3. As a plain data field's descriptor, to attach metadata or a default.
            title: str = bramble.field(default="untitled", description="The post title")

    Arguments:
        resolver: the function backing this field. A resolver's parameters are classified by
            annotation: `Parent[T]` receives the parent value, `Info` the execution context,
            `Annotated[T, bramble.Depends(...)]` an injected dependency, and everything else
            becomes a GraphQL argument. See `docs/types/resolvers.md`.
        name: the GraphQL-facing field name, overriding the camelCased Python identifier.
        description: rendered as the field's SDL description and reported by introspection.
        deprecation_reason: marks the field `@deprecated`. It keeps working; introspection hides
            it unless a client passes `fields(includeDeprecated: true)`.
        permission_classes: `bramble.BasePermission` subclasses checked before the resolver runs,
            in order, short-circuiting on the first denial.
        metadata: an arbitrary mapping stored on the underlying `dataclasses.Field`, for your own
            tooling. bramble never reads it.
        directives: applied schema-directive instances, checked against `FIELD_DEFINITION`.
        extensions: reserved; currently rejected (there is no execution hook point for it yet).
        default / default_factory: a default for a data field, as on any dataclass. Mutually
            exclusive with each other, and with `resolver`.

    Returns `Any` rather than `Field` so a type checker accepts it as the annotated field's own
    declared type.
    """
    return Field(
        resolver,
        name=name,
        description=description,
        deprecation_reason=deprecation_reason,
        permission_classes=permission_classes,
        directives=directives,
        extensions=extensions,
        metadata=metadata,
        default=default,
        default_factory=default_factory,
    )


def mutation(*args: Any, **kwargs: Any) -> Any:
    """An alias for `bramble.field`, for declaring a field on the mutation root type.

    Purely for readability at the declaration site -- nothing downstream distinguishes a field
    declared this way from one declared with `bramble.field`.
    """
    return field(*args, **kwargs)


def subscription(*args: Any, **kwargs: Any) -> Any:
    """An alias for `bramble.field`, for declaring a field on the subscription root type.

    Purely for readability at the declaration site -- a subscription field is an ordinary field
    whose resolver happens to be an async generator, and nothing downstream distinguishes one
    declared this way from one declared with `bramble.field`. Exists so the three root types read
    symmetrically (`bramble.field` / `bramble.mutation` / `bramble.subscription`).
    """
    return field(*args, **kwargs)


def _ensure_field_annotations(cls: _type) -> None:
    """dataclasses.dataclass() only recognizes an annotated class attribute as a field. A
    method-style resolver (`@bramble.field` applied directly to a method) has no such
    annotation, so inject one from its return type before handing the class off.
    """
    annotations = inspect.get_annotations(cls)
    changed = False
    for attribute_name, value in vars(cls).items():
        if attribute_name in annotations or not isinstance(value, Field) or value.resolver is None:
            continue

        return_annotation = getattr(value.resolver, "__annotations__", {}).get("return")
        if return_annotation is None:
            raise SchemaError(
                f"field '{attribute_name}' on '{cls.__name__}' has no return type annotation"
            )

        annotations[attribute_name] = return_annotation
        changed = True

    if changed:
        cls.__annotations__ = annotations


def _restore_resolvers(cls: _type) -> None:
    """dataclasses.dataclass() strips a resolver-backed field's class attribute (its `init=False`
    default is `MISSING`, so there's nothing left to leave in place) -- restore the resolver
    callable afterward so `Cls.field_name`/`instance.field_name()` still works.
    """
    for dataclass_field in dataclasses.fields(cls):
        resolver = getattr(dataclass_field, "resolver", None)
        if resolver is not None:
            setattr(cls, dataclass_field.name, resolver)


def _validate_directive_locations(directives: Sequence[object], required_location: Location, owner_name: str) -> None:
    """A schema directive declares which locations it's legal to use at (§6); check the
    directives applied here against `required_location`. Only recognizes objects produced by
    `@bramble.schema_directive` (anything else -- e.g. a future non-bramble metadata object -- is
    skipped rather than rejected).
    """
    for directive in directives:
        info = getattr(_type(directive), "__bramble_directive_info__", None)
        if info is None:
            continue
        if required_location.value not in info.locations:
            raise SchemaError(
                f"directive '@{info.name}' cannot be applied to '{owner_name}' ({required_location.value}); "
                f"declared locations: {', '.join(info.locations)}"
            )


def _validate_field_directive_locations(cls: _type) -> None:
    """Same check as `_validate_directive_locations`, but for each field's own
    `bramble.field(directives=[...])` against `FIELD_DEFINITION` -- distinct from the type-level
    check, which validates directives applied to the type itself. A plain (non-`bramble.field`)
    dataclass field has no `.directives` attribute at all, hence the `getattr` default.
    """
    for dataclass_field in dataclasses.fields(cls):
        field_directives = getattr(dataclass_field, "directives", ())
        _validate_directive_locations(
            field_directives, Location.FIELD_DEFINITION, f"{cls.__name__}.{dataclass_field.name}"
        )


def _process_type(
    cls: _type | None,
    *,
    kind: Literal["type", "interface", "input"],
    name: str | None = None,
    description: str | None = None,
    directives: Sequence[object] = (),
    one_of: bool = False,
) -> Callable[[_type], _type] | _type:
    def wrap(cls: _type) -> _type:
        _ensure_field_annotations(cls)
        cls = dataclasses.dataclass(cls, kw_only=True)
        _restore_resolvers(cls)
        _validate_directive_locations(directives, _LOCATION_BY_KIND[kind], cls.__name__)
        _validate_field_directive_locations(cls)

        cls.__bramble_type_info__ = process_type(
            cls,
            kind=kind,
            name=name,
            description=description,
            directives=tuple(directives),
            one_of=one_of,
        )
        # `process_type` (Rust) already extracted these directives' *values* into
        # `TypeDefinition.applied_directives` for SDL rendering -- kept here too, on the Python
        # side, so `Schema()`'s graph walker (§7b) can discover each *class* of schema directive
        # actually in use (locations/field defs, for its own `directive @name(...) on ...`
        # declaration), which the already-extracted values alone can't reconstruct.
        cls.__bramble_applied_directives__ = tuple(directives)
        # Keyed by Python field name. Execution reaches a field through Rust's `FieldInfo`, which
        # deliberately carries no Python callables -- permissions are an execution-time concern, so
        # they ride here rather than being threaded through the schema IR.
        cls.__bramble_permissions__ = {
            dataclass_field.name: permissions
            for dataclass_field in dataclasses.fields(cls)
            if (permissions := getattr(dataclass_field, "permission_classes", ()))
        }
        return cls

    if cls is None:
        return wrap
    return wrap(cls)


def type(
    cls: _type | None = None,
    *,
    name: str | None = None,
    description: str | None = None,
    directives: Sequence[object] = (),
) -> Callable[[_type], _type] | _type:
    """Declares a class as a GraphQL object type.

        @bramble.type
        class User:
            id: bramble.ID
            name: str

            @bramble.field
            def display_name(parent: bramble.Parent["User"]) -> str:
                return parent.name.title()

    The class becomes a real dataclass (kw-only), so `User(id=..., name=...)`, `dataclasses.fields`,
    `__eq__`, and `__repr__` all work as usual. Each annotated attribute becomes a GraphQL field,
    named by camelCasing the Python identifier unless `SchemaConfig(auto_camel_case=False)` or an
    explicit `bramble.field(name=...)` says otherwise. Annotate an attribute `bramble.Private[T]` to
    keep it off the schema entirely.

    Usable bare (`@bramble.type`) or with arguments (`@bramble.type(name=...)`).

    Arguments:
        name: the GraphQL type name, overriding the Python class name.
        description: rendered as the type's SDL description.
        directives: applied schema-directive instances, checked against `OBJECT`.
    """
    return _process_type(cls, kind="type", name=name, description=description, directives=directives)


def interface(
    cls: _type | None = None,
    *,
    name: str | None = None,
    description: str | None = None,
    directives: Sequence[object] = (),
) -> Callable[[_type], _type] | _type:
    """Declares a class as a GraphQL interface.

        @bramble.interface
        class Node:
            id: bramble.ID

        @bramble.type
        class User(Node):        # implements Node by inheriting from it
            name: str

    A type implements an interface by **inheriting** from it -- there is no `implements=[...]` list
    to keep in sync, and dataclass field inheritance means an implementor structurally cannot omit
    an interface field. Conformance (nullability and argument covariance) is checked when the
    `Schema` is built.

    At execution time, an interface-typed field routes to the right concrete class via each
    implementor's optional `is_type_of(obj, info)` classmethod, falling back to `isinstance`.
    Exactly one implementor must match.

    Otherwise identical to `bramble.type`; see it for the shared arguments (`directives` is checked
    against `INTERFACE` here).
    """
    return _process_type(cls, kind="interface", name=name, description=description, directives=directives)


def input(
    cls: _type | None = None,
    *,
    name: str | None = None,
    description: str | None = None,
    directives: Sequence[object] = (),
    one_of: bool = False,
) -> Callable[[_type], _type] | _type:
    """Declares a class as a GraphQL input object, for use as a resolver argument type.

        @bramble.input
        class PostFilter:
            author_id: bramble.ID | None = None
            limit: int = 10

    Input types carry data only: a field with a resolver is rejected. An incoming argument literal
    is coerced into a real instance of this class before the resolver sees it, so a resolver
    annotated `filter: PostFilter` receives an instance, never a bare dict.

    Arguments:
        one_of: renders `@oneOf`, declaring that exactly one field may be supplied.

    See `bramble.type` for the remaining arguments (`directives` is checked against `INPUT_OBJECT`).
    """
    return _process_type(
        cls,
        kind="input",
        name=name,
        description=description,
        directives=directives,
        one_of=one_of,
    )


def asdict(instance: Any) -> dict[str, Any]:
    """Converts a `@bramble.type`/`@bramble.input` instance into a plain dict.

    A thin pass-through to `dataclasses.asdict` -- bramble types are real dataclasses, so this
    exists for discoverability rather than because it does anything extra. Recurses into nested
    types, lists, and dicts, as `dataclasses.asdict` does.
    """
    return dataclasses.asdict(instance)
