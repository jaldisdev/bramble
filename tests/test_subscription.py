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
from collections.abc import AsyncGenerator, AsyncIterator

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

    schema = bramble.Schema(query=Query, subscription=Subscription, types=[_Message])

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

    with pytest.raises(bramble.GraphQLError, match="exactly one root-level field"):
        asyncio.run(run())


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
