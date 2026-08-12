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

from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Generic, TypeVar

# A real import, not a `TYPE_CHECKING` one: the compiled module has no cycle with this one, and
# `Info`'s annotations must stay resolvable at runtime (see `_schema`'s late binding of `Schema`
# for the one name that genuinely can't be).
from bramble._bramble import GraphQLTypeInfo

if TYPE_CHECKING:
    # `Schema` is the one name here that genuinely cannot be imported at runtime: `_schema` imports
    # `_execution`, which imports this module. It is instead bound late, by `_schema` itself once
    # the class exists -- see the assignment at the bottom of `bramble/_schema.py`, which is what
    # keeps `typing.get_type_hints(Info)` working.
    from bramble._schema import Schema

# `Path` and `SelectedField` live here rather than in `bramble._execution` where they are built:
# both are part of the *resolver-facing* surface (they are what `Info.path`/`Info.selected_fields`
# hand a resolver), and defining them here is what lets `Info`'s own annotations reference them
# without an import cycle. `bramble._execution` re-exports them, so the older import path still
# works.
@dataclass(frozen=True, slots=True)
class Path:
    """One segment of a GraphQL response path (§8's `path` field), linked back to its parent --
    mirrors graphql-core's own `Path` rather than a plain list, so building one for a deeply
    nested field is O(1) (append a segment) instead of O(depth) (copy-and-append a list).
    """

    key: str | int
    prev: "Path | None" = None

    def as_list(self) -> list[str | int]:
        segments: list[str | int] = []
        node: Path | None = self
        while node is not None:
            segments.append(node.key)
            node = node.prev
        segments.reverse()
        return segments


@dataclass(frozen=True, slots=True)
class SelectedField:
    """A read-only view of one of the current field's own sub-selections (`Info.selected_fields`),
    for a resolver that wants to inspect what's being asked of it (e.g. to avoid fetching a column
    nothing selected). Only one level deep -- each entry's own `selections` goes one level further,
    same as the query itself nests.
    """

    name: str
    arguments: dict[str, Any]
    selections: list["SelectedField"]


ParentType = TypeVar("ParentType")
ContextType = TypeVar("ContextType")
RootValueType = TypeVar("RootValueType")
StreamedItemType = TypeVar("StreamedItemType")
ProvidedType = TypeVar("ProvidedType")


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
    #: The `@bramble.type`-decorated class this field is being resolved on -- the *concrete* one,
    #: already dispatched, when the field's declared type is an interface or union.
    parent_type: type
    #: This field's declared GraphQL type, wrappers included (`[Post!]!`).
    return_type: GraphQLTypeInfo
    #: The operation type currently executing: `"query"`, `"mutation"`, or `"subscription"`.
    operation: str
    context: ContextType
    root_value: RootValueType
    variable_values: dict[str, Any]
    query: str | None
    path: "Path"
    selected_fields: list["SelectedField"]
    schema: "Schema"


class Depends(Generic[ProvidedType]):
    """Marker used in a resolver (or custom operation directive, or another provider's own)
    parameter's annotation to receive a value produced by `provider` -- dependency injection (§3c),
    a bramble addition not present in Strawberry:

        async def get_gel_client(info: bramble.Info) -> AsyncIterator[GelClient]:
            client = await create_gel_client(info.context["dsn"])
            try:
                yield client
            finally:
                await client.aclose()

        @bramble.field
        async def some_query(
            client: Annotated[GelClient, bramble.Depends(get_gel_client)],
        ) -> SomeResult:
            return await client.query(...)

    Only ever appears as `Annotated[T, bramble.Depends(provider)]` metadata (never a bare
    `Depends[T]` subscript the way `Parent[T]`/`Info` are) -- `Generic[T]` here is purely for a type
    checker's benefit, tying `provider`'s own return type to the annotated parameter's type.

    `provider` may be a plain function returning `T`, an `async def` returning (an awaitable of)
    `T`, or an async-generator function `yield`-ing exactly one `T` (for setup/teardown around it,
    `try`/`finally` style, as `get_gel_client` above does) -- see `bramble._dependency` for the
    runtime resolution, caching, and teardown semantics.

    `use_cache=False` opts this specific injection site out of the per-request/per-subscription
    cache (both reading and writing it) -- matches FastAPI's own `use_cache` behavior, including
    that it's per-injection-site, not per-provider globally.
    """

    def __init__(
        self,
        provider: Callable[..., "ProvidedType | Awaitable[ProvidedType] | AsyncIterator[ProvidedType]"],
        *,
        use_cache: bool = True,
    ) -> None:
        self.provider = provider
        self.use_cache = use_cache


class Argument:
    """What `bramble.argument(...)` produces -- metadata for one resolver argument, attached to its
    annotation via `typing.Annotated`.
    """

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
    """Customises one resolver argument, independently of its Python parameter name.

        @bramble.field
        def greet(
            name: Annotated[str, bramble.argument(name="who", description="Who to greet")] = "world",
        ) -> str:
            return f"Hello, {name}!"

    Works on any resolver argument, and on a custom operation directive's arguments too.

    Arguments:
        name: the GraphQL argument name, overriding the camelCased parameter name.
        description: rendered inline in SDL and reported by introspection.
        deprecation_reason: marks the argument `@deprecated`.
        graphql_type: overrides the GraphQL type derived from the annotation.
        directives: applied schema-directive instances, checked against `ARGUMENT_DEFINITION`.
    """
    return Argument(
        name=name,
        description=description,
        deprecation_reason=deprecation_reason,
        graphql_type=graphql_type,
        directives=directives,
    )
