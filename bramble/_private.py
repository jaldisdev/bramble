from __future__ import annotations

import typing
from typing import Annotated, TypeVar


class PrivateMarker:
    """Marks a field as excluded from the generated GraphQL schema. Not meant to be used
    directly -- see `Private[T]`.
    """


T = TypeVar("T")

Private = Annotated[T, PrivateMarker()]
"""A field type wrapper that excludes the field from the generated GraphQL schema entirely -- it
stays a normal Python attribute (still participates in `__init__`/equality/repr like any other
dataclass field), just invisible to any query:

    @bramble.type
    class User:
        name: str
        age: bramble.Private[int]

Combining `Private` with an explicit `bramble.field(...)` (a resolver, description, directives,
...) is a `SchemaError` -- a field excluded from the schema can't also carry schema-facing
configuration.
"""


def is_private(annotation: object) -> bool:
    return typing.get_origin(annotation) is Annotated and any(
        isinstance(argument, PrivateMarker) for argument in typing.get_args(annotation)
    )
