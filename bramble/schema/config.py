from __future__ import annotations

import dataclasses
from typing import Any

from bramble._scalar import ScalarDefinition


@dataclasses.dataclass(kw_only=True)
class SchemaConfig:
    scalar_map: dict[Any, ScalarDefinition] = dataclasses.field(default_factory=dict)
    # Default: a field/argument with no explicit `name=` override is queried by a camelCase
    # rendering of its Python identifier (`post_id` -> `postId`), not the raw identifier. Set
    # `False` to keep the raw identifier as the GraphQL-facing name instead.
    auto_camel_case: bool = True
