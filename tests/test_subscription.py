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

import asyncio
from collections.abc import AsyncGenerator, AsyncIterable, AsyncIterator

import pytest

import bramble

# `typing.get_type_hints` can't see an enclosing test function's local scope, so any class
# referenced *from another class's annotation* (a resolver's own return type) has to live at
# module level here -- matches the rest of the test suite's own established convention.


@bramble.type
class _Message:
    @bramble.field
    def text(parent: bramble.Parent[object]) -> str:
        if parent.text == "bad":  # type: ignore[attr-defined]
            raise ValueError("boom")
        return parent.text  # type: ignore[attr-defined]


class _MessageEvent:
    def __init__(self, text: str) -> None:
        self.text = text


async def _collect(generator: AsyncIterator[dict]) -> list[dict]:
    return [response async for response in generator]


def test_subscription_yields_one_response_per_event() -> None:
    @bramble.type
    class Query:
        ok: bool = True

    @bramble.type
    class Subscription:
        @bramble.field
        async def count(upto: int) -> AsyncGenerator[int, None]:
            for i in range(upto):
                yield i

    schema = bramble.Schema(query=Query, subscription=Subscription)

    responses = asyncio.run(_collect(schema.subscribe_async("subscription { count(upto: 3) }")))

    assert responses == [
        {"data": {"count": 0}},
        {"data": {"count": 1}},
        {"data": {"count": 2}},
    ]


def test_subscription_field_can_have_nested_selections() -> None:
    @bramble.type
    class Query:
        ok: bool = True

    @bramble.type
    class Subscription:
        @bramble.field
        async def messages() -> AsyncGenerator[_Message, None]:
            yield _MessageEvent("hi")
            yield _MessageEvent("bye")

    # Deliberately no `types=[_Message]`: `_Message` is reachable only through this field's own
    # `AsyncGenerator[...]` return annotation, so this exercises discovery walking *through* the
    # async wrapper rather than papering over it (see this file's own discovery tests below).
    schema = bramble.Schema(query=Query, subscription=Subscription)

    responses = asyncio.run(_collect(schema.subscribe_async("subscription { messages { text } }")))

    assert responses == [
        {"data": {"messages": {"text": "hi"}}},
        {"data": {"messages": {"text": "bye"}}},
    ]


def test_error_completing_one_event_does_not_end_the_subscription() -> None:
    @bramble.type
    class Query:
        ok: bool = True

    @bramble.type
    class Subscription:
        @bramble.field
        async def messages() -> AsyncGenerator[_Message, None]:
            yield _MessageEvent("hi")
            yield _MessageEvent("bad")
            yield _MessageEvent("bye")

    schema = bramble.Schema(query=Query, subscription=Subscription, types=[_Message])

    responses = asyncio.run(_collect(schema.subscribe_async("subscription { messages { text } }")))

    assert len(responses) == 3
    assert responses[0] == {"data": {"messages": {"text": "hi"}}}
    assert responses[1]["data"] is None
    assert responses[1]["errors"][0]["message"] == "boom"
    assert responses[2] == {"data": {"messages": {"text": "bye"}}}


def test_execute_async_rejects_a_subscription_operation() -> None:
    @bramble.type
    class Query:
        ok: bool = True

    @bramble.type
    class Subscription:
        @bramble.field
        async def count() -> AsyncGenerator[int, None]:
            yield 1

    schema = bramble.Schema(query=Query, subscription=Subscription)

    async def run() -> None:
        await schema.execute_async("subscription { count }")

    with pytest.raises(bramble.GraphQLError, match="subscribe_async"):
        asyncio.run(run())


def test_subscribe_async_rejects_a_query_operation() -> None:
    @bramble.type
    class Query:
        ok: bool = True

    @bramble.type
    class Subscription:
        @bramble.field
        async def count() -> AsyncGenerator[int, None]:
            yield 1

    schema = bramble.Schema(query=Query, subscription=Subscription)

    async def run() -> list[dict]:
        return await _collect(schema.subscribe_async("{ ok }"))

    with pytest.raises(bramble.GraphQLError, match="execute_async"):
        asyncio.run(run())


def test_subscription_with_more_than_one_root_field_is_rejected() -> None:
    @bramble.type
    class Query:
        ok: bool = True

    @bramble.type
    class Subscription:
        @bramble.field
        async def count() -> AsyncGenerator[int, None]:
            yield 1

    schema = bramble.Schema(query=Query, subscription=Subscription)

    async def run() -> list[dict]:
        return await _collect(schema.subscribe_async("subscription { a: count b: count }"))

    # Caught by Rust validation now, before execution ever starts, so it reports a source location
    # like every other validation error rather than surfacing only once the executor got there.
    with pytest.raises(bramble.GraphQLError, match="exactly one root field"):
        asyncio.run(run())


def test_subscription_root_field_count_behind_skip_include_is_caught_at_execution() -> None:
    """The static Rust check deliberately abstains when a root selection carries `@skip`/`@include`:
    the real count depends on variable values it doesn't have. `subscribe_async`'s own check is the
    post-pruning backstop that still has to catch it.
    """

    @bramble.type
    class Query:
        ok: bool = True

    @bramble.type
    class Subscription:
        @bramble.field
        async def count() -> AsyncGenerator[int, None]:
            yield 1

    schema = bramble.Schema(query=Query, subscription=Subscription)

    async def run() -> list[dict]:
        # `@skip(if: false)` keeps both fields, so two root fields survive lowering.
        return await _collect(schema.subscribe_async("subscription { a: count b: count @skip(if: false) }"))

    with pytest.raises(bramble.GraphQLError, match="exactly one root-level field"):
        asyncio.run(run())


def test_subscription_root_field_pruned_by_skip_down_to_one_still_runs() -> None:
    @bramble.type
    class Query:
        ok: bool = True

    @bramble.type
    class Subscription:
        @bramble.field
        async def count() -> AsyncGenerator[int, None]:
            yield 1

    schema = bramble.Schema(query=Query, subscription=Subscription)

    async def run() -> list[dict]:
        return await _collect(schema.subscribe_async("subscription { a: count b: count @skip(if: true) }"))

    assert asyncio.run(run()) == [{"data": {"a": 1}}]


def test_non_async_generator_subscription_resolver_raises_a_clear_error() -> None:
    @bramble.type
    class Query:
        ok: bool = True

    @bramble.type
    class Subscription:
        @bramble.field
        async def count() -> int:
            return 1

    schema = bramble.Schema(query=Query, subscription=Subscription)

    async def run() -> list[dict]:
        return await _collect(schema.subscribe_async("subscription { count }"))

    with pytest.raises(bramble.GraphQLError, match="async generator"):
        asyncio.run(run())


# --- Type discovery through async wrapper annotations ---------------------------------------------
#
# A field's declared type is resolved *through* an async wrapper rather than to it -- Rust's
# `resolve_core` unwraps `AsyncGenerator[T, ...]`/`AsyncIterator[T]`/`AsyncIterable[T]` to `T` and
# `Streamable[T]` to `[T]` (`crates/bramble-py/src/typing_utils.rs`). Discovery
# (`bramble._schema._discover_annotation`) has to look through the identical set, or a type
# reachable *only* through one of them is named by the field yet missing from the schema entirely:
# the SDL renders `events: Payload!` with no `type Payload { ... }` anywhere, and executing a
# selection against it hands back the raw Python object instead of a projected dict (which then
# fails to JSON-serialize in any transport). Each test below therefore deliberately passes no
# `types=[...]`, so discovery is the only thing that can find the payload type.


@bramble.type
class _Payload:
    value: str


@bramble.type
class _DiscoveryQuery:
    ok: bool = True


def _payload_type_is_defined(schema: bramble.Schema) -> bool:
    """Whether the SDL actually *defines* `_Payload`, not merely names it as some field's type --
    the exact distinction this whole section is about.
    """
    return "type _Payload {" in schema.to_sdl()


def test_async_generator_payload_type_is_discovered() -> None:
    @bramble.type
    class Subscription:
        @bramble.field
        async def events() -> AsyncGenerator[_Payload, None]:
            yield _Payload(value="a")

    schema = bramble.Schema(query=_DiscoveryQuery, subscription=Subscription)

    assert "_Payload" in schema.types_by_name
    assert _payload_type_is_defined(schema)

    responses = asyncio.run(_collect(schema.subscribe_async("subscription { events { value } }")))
    assert responses == [{"data": {"events": {"value": "a"}}}]


def test_async_iterator_payload_type_is_discovered() -> None:
    @bramble.type
    class Subscription:
        @bramble.field
        async def events() -> AsyncIterator[_Payload]:
            yield _Payload(value="a")

    schema = bramble.Schema(query=_DiscoveryQuery, subscription=Subscription)

    assert "_Payload" in schema.types_by_name
    assert _payload_type_is_defined(schema)

    responses = asyncio.run(_collect(schema.subscribe_async("subscription { events { value } }")))
    assert responses == [{"data": {"events": {"value": "a"}}}]


def test_async_iterable_payload_type_is_discovered() -> None:
    @bramble.type
    class Subscription:
        @bramble.field
        async def events() -> AsyncIterable[_Payload]:
            yield _Payload(value="a")

    schema = bramble.Schema(query=_DiscoveryQuery, subscription=Subscription)

    assert "_Payload" in schema.types_by_name
    assert _payload_type_is_defined(schema)

    responses = asyncio.run(_collect(schema.subscribe_async("subscription { events { value } }")))
    assert responses == [{"data": {"events": {"value": "a"}}}]


def test_streamable_element_type_is_discovered() -> None:
    """`Streamable[T]` is a *query* field's annotation (a `@stream`-capable list), not a
    subscription's -- but it goes through the same unwrapping, so it needs the same discovery.
    """

    @bramble.type
    class Query:
        @bramble.field
        async def items() -> bramble.Streamable[_Payload]:
            yield _Payload(value="a")

    schema = bramble.Schema(query=Query)

    assert "_Payload" in schema.types_by_name
    assert _payload_type_is_defined(schema)
    assert "items: [_Payload!]!" in schema.to_sdl()


def test_type_nested_inside_an_async_wrapper_is_discovered() -> None:
    """Locks in that discovery *recurses* through the wrapper's arguments rather than taking a
    single-level `get_args(...)[0]`: the payload type here is two layers down
    (`AsyncGenerator[list[_Payload], None]`), reachable only by falling through to the container
    branch after unwrapping the async one.
    """

    @bramble.type
    class Subscription:
        @bramble.field
        async def events() -> AsyncGenerator[list[_Payload], None]:
            yield [_Payload(value="a")]

    schema = bramble.Schema(query=_DiscoveryQuery, subscription=Subscription)

    assert "_Payload" in schema.types_by_name
    assert "events: [_Payload!]!" in schema.to_sdl()

    responses = asyncio.run(_collect(schema.subscribe_async("subscription { events { value } }")))
    assert responses == [{"data": {"events": [{"value": "a"}]}}]


def test_subscription_source_generator_is_closed_on_unsubscribe_not_at_gc() -> None:
    """A subscription resolver's own `finally` -- unsubscribing from a broker, closing a cursor --
    must run when the consumer disconnects, not whenever the GC eventually finalizes the generator.
    `async for` alone never closes what it iterates, so this used to be deferred indefinitely.
    """
    closed: list[str] = []

    @bramble.type
    class Query:
        ok: bool = True

    @bramble.type
    class Subscription:
        @bramble.field
        async def ticks() -> AsyncGenerator[int, None]:
            try:
                index = 0
                while True:
                    yield index
                    index += 1
                    await asyncio.sleep(0)
            finally:
                closed.append("torn down")

    schema = bramble.Schema(query=Query, subscription=Subscription)

    async def run() -> None:
        generator = schema.subscribe_async("subscription { ticks }")
        seen = 0
        async for _ in generator:
            seen += 1
            if seen == 2:
                break
        assert closed == [], "not torn down while still subscribed"
        await generator.aclose()
        # Synchronously after aclose() -- no GC pass, no event-loop turn in between.
        assert closed == ["torn down"]

    asyncio.run(run())


def test_subscription_source_generator_is_closed_when_the_stream_ends_normally() -> None:
    closed: list[str] = []

    @bramble.type
    class Query:
        ok: bool = True

    @bramble.type
    class Subscription:
        @bramble.field
        async def ticks() -> AsyncGenerator[int, None]:
            try:
                yield 1
            finally:
                closed.append("torn down")

    schema = bramble.Schema(query=Query, subscription=Subscription)

    async def run() -> list[dict]:
        return await _collect(schema.subscribe_async("subscription { ticks }"))

    assert asyncio.run(run()) == [{"data": {"ticks": 1}}]
    assert closed == ["torn down"]
