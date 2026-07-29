from __future__ import annotations

import dataclasses
from collections.abc import Callable, Sequence
from typing import Any


@dataclasses.dataclass(frozen=True, kw_only=True)
class ScalarDefinition:
    name: str | None
    description: str | None
    specified_by_url: str | None
    serialize: Callable[[Any], Any] | None
    parse_value: Callable[[Any], Any] | None
    parse_literal: Callable[[Any], Any] | None
    directives: tuple[object, ...]


def scalar(
    *,
    name: str | None = None,
    description: str | None = None,
    specified_by_url: str | None = None,
    serialize: Callable[[Any], Any] | None = None,
    parse_value: Callable[[Any], Any] | None = None,
    parse_literal: Callable[[Any], Any] | None = None,
    directives: Sequence[object] = (),
) -> ScalarDefinition:
    return ScalarDefinition(
        name=name,
        description=description,
        specified_by_url=specified_by_url,
        serialize=serialize,
        parse_value=parse_value,
        parse_literal=parse_literal,
        directives=tuple(directives),
    )
