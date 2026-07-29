from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, Literal

from bramble._bramble import process_type

_type = type  # capture the builtin before `type` (below) shadows this module's name for it


class Field:
    def __init__(
        self,
        resolver: Callable[..., Any] | None = None,
        *,
        name: str | None = None,
        description: str | None = None,
        directives: Sequence[object] = (),
        extensions: Sequence[object] = (),
    ) -> None:
        self.resolver = resolver
        self.name = name
        self.description = description
        self.directives = tuple(directives)
        self.extensions = tuple(extensions)

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
) -> Any:
    return Field(
        resolver,
        name=name,
        description=description,
        directives=directives,
        extensions=extensions,
    )


def mutation(*args: Any, **kwargs: Any) -> Any:
    return field(*args, **kwargs)


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
