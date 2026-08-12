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
from collections.abc import Callable
from typing import Any, TypedDict

from bramble._resolver import Info
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
    """Schema-wide settings, passed as `bramble.Schema(config=...)`.

        SchemaConfig(
            scalar_map={Base64: bramble.scalar(name="Base64", ...)},
            auto_camel_case=True,
            batching_config={"max_operations": 10},
        )

    Attributes:
        scalar_map: maps a Python type to the `ScalarDefinition` describing it. Required only to
            declare a custom scalar in SDL/introspection, not for it to work at execution.
        default_resolver: how a resolver-less field reads its value off the parent object.
            Defaults to `getattr`; pass `lambda parent, name: parent[name]` for dict-backed parents.
        info_class: the class used for the `Info` handed to resolvers. Must be `bramble.Info` or a
            subclass.
        auto_camel_case: whether a field/argument with no explicit `name=` is exposed as a
            camelCase rendering of its Python identifier (`post_id` -> `postId`). Default `True`.
        batching_config: opt in to executing a JSON array of operations in one HTTP request.
            `None` (the default) rejects batched requests.
        validate_queries: whether every incoming query is validated against the schema before it
            executes. Default `True`, and it should stay that way -- see the attribute's own note
            below for what turning it off actually costs.
    """

    scalar_map: dict[Any, ScalarDefinition] = dataclasses.field(default_factory=dict)
    # How a field with no resolver reads its value off the parent. `getattr` suits the ordinary
    # case (a bramble type is a dataclass); override with `lambda parent, name: parent[name]` for
    # dict-backed parents, or anything else with the same `(parent, name) -> value` shape.
    default_resolver: Callable[[Any, str], Any] = getattr
    # The class instantiated for the `Info` passed to resolvers. Must be `bramble.Info` or a
    # subclass -- useful for attaching your own helpers/properties to it.
    info_class: type = Info
    # Default: a field/argument with no explicit `name=` override is queried by a camelCase
    # rendering of its Python identifier (`post_id` -> `postId`), not the raw identifier. Set
    # `False` to keep the raw identifier as the GraphQL-facing name instead.
    auto_camel_case: bool = True
    batching_config: BatchingConfig | None = None
    # Transitional escape hatch, not a performance knob: validation is what turns a malformed or
    # schema-violating query into one clear error before any resolver runs. With it off, the same
    # query still fails -- just later, deeper, and as whatever the executor happens to raise when it
    # reaches an unknown field, a missing required argument, or an argument of the wrong type. Its
    # one legitimate use is porting a schema that ran unvalidated elsewhere (Strawberry's
    # `DisableValidation`, say): keep the existing behavior on cutover day, then turn validation on
    # as its own change, with its own way to find what it surfaces. Applies to `execute_async`/
    # `execute_incremental`/`subscribe_async` and to registering an Automatic Persisted Query;
    # `Schema.validate_query()` always validates, since asking for it *is* the point of that call.
    validate_queries: bool = True

    def __post_init__(self) -> None:
        if not issubclass(self.info_class, Info):
            raise TypeError("`info_class` must be bramble.Info or a subclass")
