from __future__ import annotations

import dataclasses
import inspect
import sys
from collections.abc import Callable, Sequence
from typing import Any, Literal

from bramble._bramble import SchemaError, process_type

_type = type  # capture the builtin before `type` (below) shadows this module's name for it


class Field(dataclasses.Field):
    def __init__(
        self,
        resolver: Callable[..., Any] | None = None,
        *,
        name: str | None = None,
        description: str | None = None,
        directives: Sequence[object] = (),
        extensions: Sequence[object] = (),
        default: Any = dataclasses.MISSING,
        default_factory: Any = dataclasses.MISSING,
    ) -> None:
        if default is not dataclasses.MISSING and default_factory is not dataclasses.MISSING:
            raise SchemaError("a field cannot specify both 'default' and 'default_factory'")
        if resolver is not None and (default is not dataclasses.MISSING or default_factory is not dataclasses.MISSING):
            raise SchemaError("a field with a resolver cannot also declare a default value")

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
            metadata=None,
            **kwargs,
        )

        # `dataclasses.Field.name` is reserved for the Python attribute name (assigned by
        # dataclasses itself while processing the class); the GraphQL name override lives
        # under a different attribute to avoid clobbering it.
        self.graphql_name = name
        self.description = description
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
    directives: Sequence[object] = (),
    extensions: Sequence[object] = (),
    default: Any = dataclasses.MISSING,
    default_factory: Any = dataclasses.MISSING,
) -> Any:
    return Field(
        resolver,
        name=name,
        description=description,
        directives=directives,
        extensions=extensions,
        default=default,
        default_factory=default_factory,
    )


def mutation(*args: Any, **kwargs: Any) -> Any:
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

        cls.__bramble_type_info__ = process_type(
            cls,
            kind=kind,
            name=name,
            description=description,
            directives=tuple(directives),
            one_of=one_of,
        )
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
    return _process_type(cls, kind="type", name=name, description=description, directives=directives)


def interface(
    cls: _type | None = None,
    *,
    name: str | None = None,
    description: str | None = None,
    directives: Sequence[object] = (),
) -> Callable[[_type], _type] | _type:
    return _process_type(cls, kind="interface", name=name, description=description, directives=directives)


def input(
    cls: _type | None = None,
    *,
    name: str | None = None,
    description: str | None = None,
    directives: Sequence[object] = (),
    one_of: bool = False,
) -> Callable[[_type], _type] | _type:
    return _process_type(
        cls,
        kind="input",
        name=name,
        description=description,
        directives=directives,
        one_of=one_of,
    )
