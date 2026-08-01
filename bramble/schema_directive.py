from __future__ import annotations

import dataclasses
import enum
import sys
from collections.abc import Sequence
from typing import Any

from bramble._bramble import describe_schema_directive

_type = type  # capture the builtin before it's shadowed by a `type` parameter's annotation


class Location(enum.Enum):
    SCHEMA = "SCHEMA"
    SCALAR = "SCALAR"
    OBJECT = "OBJECT"
    FIELD_DEFINITION = "FIELD_DEFINITION"
    ARGUMENT_DEFINITION = "ARGUMENT_DEFINITION"
    INTERFACE = "INTERFACE"
    UNION = "UNION"
    ENUM = "ENUM"
    ENUM_VALUE = "ENUM_VALUE"
    INPUT_OBJECT = "INPUT_OBJECT"
    INPUT_FIELD_DEFINITION = "INPUT_FIELD_DEFINITION"


class DirectiveField(dataclasses.Field):
    def __init__(self, name: str, *, default: Any = dataclasses.MISSING) -> None:
        kwargs: dict[str, Any] = {"kw_only": True}
        if sys.version_info >= (3, 14):
            kwargs["doc"] = None

        super().__init__(
            default=default,
            default_factory=dataclasses.MISSING,
            init=True,
            repr=True,
            compare=True,
            hash=None,
            metadata=None,
            **kwargs,
        )
        self.graphql_name = name


def directive_field(name: str, *, default: Any = dataclasses.MISSING) -> Any:
    return DirectiveField(name, default=default)


def schema_directive(
    locations: Sequence[Location],
    *,
    name: str | None = None,
    description: str | None = None,
    repeatable: bool = False,
) -> Any:
    def wrap(cls: _type) -> _type:
        cls = dataclasses.dataclass(cls, kw_only=True)
        cls.__bramble_directive_info__ = describe_schema_directive(
            cls,
            locations=[location.value for location in locations],
            name=name,
            description=description,
            repeatable=repeatable,
        )
        return cls

    return wrap
