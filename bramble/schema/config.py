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
