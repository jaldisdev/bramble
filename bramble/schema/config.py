from __future__ import annotations

import dataclasses
from typing import Any

from bramble._scalar import ScalarDefinition


@dataclasses.dataclass(kw_only=True)
class SchemaConfig:
    scalar_map: dict[Any, ScalarDefinition] = dataclasses.field(default_factory=dict)
