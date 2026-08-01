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

from collections.abc import AsyncGenerator, Sequence
from typing import Any, Generic, TypeVar

ParentType = TypeVar("ParentType")
ContextType = TypeVar("ContextType")
RootValueType = TypeVar("RootValueType")
StreamedItemType = TypeVar("StreamedItemType")


class Streamable(AsyncGenerator[StreamedItemType, None]):
    """Return-type annotation for a `@stream`-capable field's resolver -- write the resolver
    exactly like a subscription's own async-generator resolver (`async def resolver() ->
    Streamable[T]: yield ...`), but bramble treats the field's own GraphQL type as `[T!]!` (a list),
    not `T`. Deliberately distinct from a bare `AsyncGenerator[T, None]` return type (what a
    subscription root field uses instead): a subscription's each yielded event is delivered as its
    own independent top-level response, never appended to one array, so its field type unwraps to
    plain `T`; `@stream`'s yielded items *are* elements of one list, so the field type must stay
    `[T!]!`. Never instantiated -- purely a type-checker-friendly spelling of "this is an async
    generator resolver" that bramble's own schema-building can tell apart from a subscription's.
    """



class Parent(Generic[ParentType]):
    """Marker used in a resolver parameter's annotation to receive the parent/root value.

    Never instantiated -- `Parent[T]` only ever appears as a type annotation
    (`def resolver(parent: Parent[User]) -> str: ...`); the annotation itself is what the
    Rust-side signature classifier looks for.
    """


class Info(Generic[ContextType, RootValueType]):
    """Marker used in a resolver parameter's annotation to receive the execution context.

    Populated by the execution bridge for each resolver call; not constructible here.
    """

    field_name: str
    python_name: str
    context: ContextType
    root_value: RootValueType
    variable_values: dict[str, Any]
    query: str | None
    path: "Path"
    selected_fields: list["SelectedField"]
    schema: "Schema"


class Argument:
    def __init__(
        self,
        name: str | None = None,
        description: str | None = None,
        deprecation_reason: str | None = None,
        graphql_type: Any | None = None,
        directives: Sequence[object] = (),
    ) -> None:
        self.name = name
        self.description = description
        self.deprecation_reason = deprecation_reason
        self.graphql_type = graphql_type
        self.directives = tuple(directives)


def argument(
    name: str | None = None,
    description: str | None = None,
    deprecation_reason: str | None = None,
    graphql_type: Any | None = None,
    directives: Sequence[object] = (),
) -> Argument:
    return Argument(
        name=name,
        description=description,
        deprecation_reason=deprecation_reason,
        graphql_type=graphql_type,
        directives=directives,
    )
