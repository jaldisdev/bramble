from __future__ import annotations

import dataclasses
from typing import Any, TypedDict

from bramble._scalar import ScalarDefinition


class BatchingConfig(TypedDict):
    """Enables the HTTP layer (`bramble.http`) to accept a JSON array of operations in one
    request, executing each and returning a JSON array of responses in the same order. Disabled
    (`SchemaConfig.batching_config=None`) by default -- a client that doesn't need it never pays
    for the extra request-shape branching, and a server operator opts in deliberately, the same
    way any other capacity-affecting knob should be explicit rather than always-on.
    """

    max_operations: int


@dataclasses.dataclass(kw_only=True)
class SchemaConfig:
    scalar_map: dict[Any, ScalarDefinition] = dataclasses.field(default_factory=dict)
    # Default: a field/argument with no explicit `name=` override is queried by a camelCase
    # rendering of its Python identifier (`post_id` -> `postId`), not the raw identifier. Set
    # `False` to keep the raw identifier as the GraphQL-facing name instead.
    auto_camel_case: bool = True
    batching_config: BatchingConfig | None = None
