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
from collections.abc import AsyncGenerator
from typing import Any

import bramble
from bramble.subscriptions.graphql_transport_ws import GraphQLTransportWSHandler

# These drive `GraphQLTransportWSHandler` directly through a fake socket rather than through a real
# adapter: the protocol's own close codes (4400/4401/4409/4429) are the thing under test, and a
# framework's test client generally flattens or hides them.

_TORN_DOWN: list[str] = []


@bramble.type
class _Query:
    # A resolver rather than a plain data field: the WebSocket handler passes no root value, so a
    # plain field would have nothing to read from.
    @bramble.field
    def ok() -> bool:
        return True


@bramble.type
class _Subscription:
    @bramble.field
    async def ticks() -> AsyncGenerator[int, None]:
        try:
            index = 0
            while True:
                yield index
                index += 1
                await asyncio.sleep(0)
        finally:
            _TORN_DOWN.append("ticks")


class _FakeWebSocket:
    """Feeds `handle()` a scripted sequence of client messages, then blocks forever so the handler
    stays in its receive loop exactly like a real idle connection (letting the test decide when to
    stop it) rather than falling out of the loop and closing on its own.
    """

    def __init__(self, messages: list[Any], *, hang_after: bool = False) -> None:
        self.incoming = list(messages)
        self.hang_after = hang_after
        self.sent: list[dict] = []
        self.accepted_subprotocol: str | None = None
        self.closed: tuple[int, str | None] | None = None

    async def accept(self, subprotocol: str | None = None) -> None:
        self.accepted_subprotocol = subprotocol

    async def receive_json(self) -> Any:
        # A real socket receive always yields to the event loop; this one must too, or an operation
        # task created by the previous message would never get a chance to start running before the
        # next one is handled -- which would make the teardown tests below pass vacuously, against
        # tasks that were cancelled before they ever began.
        await self._let_operations_run()
        if self.incoming:
            return self.incoming.pop(0)
        if self.hang_after:
            await asyncio.Event().wait()  # never resolves
        raise ConnectionError("client disconnected")

    @staticmethod
    async def _let_operations_run() -> None:
        for _ in range(50):
            await asyncio.sleep(0)

    async def send_json(self, data: Any) -> None:
        self.sent.append(data)

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        if self.closed is None:
            self.closed = (code, reason)

    def messages_of_type(self, message_type: str) -> list[dict]:
        return [message for message in self.sent if message.get("type") == message_type]


def _schema() -> bramble.Schema:
    return bramble.Schema(query=_Query, subscription=_Subscription)


def _run(messages: list[Any]) -> _FakeWebSocket:
    websocket = _FakeWebSocket(messages)
    asyncio.run(GraphQLTransportWSHandler(schema=_schema(), websocket=websocket).handle())
    return websocket


def test_connection_init_is_acknowledged() -> None:
    websocket = _run([{"type": "connection_init"}])

    assert websocket.messages_of_type("connection_ack") == [{"type": "connection_ack"}]


def test_ping_is_answered_with_a_pong() -> None:
    """Without this a client using protocol-level keepalive gets no reply and eventually times the
    connection out on its own.
    """
    websocket = _run([{"type": "connection_init"}, {"type": "ping"}])

    assert websocket.messages_of_type("pong") == [{"type": "pong"}]


def test_ping_payload_is_echoed_back_on_the_pong() -> None:
    websocket = _run([{"type": "connection_init"}, {"type": "ping", "payload": {"n": 1}}])

    assert websocket.messages_of_type("pong") == [{"type": "pong", "payload": {"n": 1}}]


def test_ping_is_answered_even_before_connection_init() -> None:
    # A liveness check, not an operation -- it isn't gated on initialisation.
    websocket = _run([{"type": "ping"}])

    assert websocket.messages_of_type("pong") == [{"type": "pong"}]
    assert websocket.closed != (4401, "Unauthorized")


def test_an_unsolicited_pong_is_accepted_without_a_reply() -> None:
    websocket = _run([{"type": "connection_init"}, {"type": "pong"}])

    assert websocket.closed is None or websocket.closed[0] not in (4400, 4401)


def test_operations_before_connection_init_close_with_4401() -> None:
    websocket = _run([{"type": "subscribe", "id": "1", "payload": {"query": "subscription { ticks }"}}])

    assert websocket.closed == (4401, "Unauthorized")


def test_a_second_connection_init_closes_with_4429() -> None:
    websocket = _run([{"type": "connection_init"}, {"type": "connection_init"}])

    assert websocket.closed == (4429, "Too many initialisation requests")


def test_a_duplicate_subscription_id_closes_with_4409() -> None:
    """Overwriting the entry would orphan the running task -- nothing would ever cancel it, and it
    would keep streaming into the socket under an id the client believes it has just re-bound.
    """
    websocket = _run(
        [
            {"type": "connection_init"},
            {"type": "subscribe", "id": "dup", "payload": {"query": "subscription { ticks }"}},
            {"type": "subscribe", "id": "dup", "payload": {"query": "subscription { ticks }"}},
        ]
    )

    assert websocket.closed == (4409, "Subscriber for dup already exists")


def test_a_subscribe_without_an_id_closes_with_4400_instead_of_raising() -> None:
    """This used to raise `KeyError` on `message["id"]`, which the handler's blanket except
    swallowed -- the socket closed with no code and no diagnostic.
    """
    websocket = _run([{"type": "connection_init"}, {"type": "subscribe", "payload": {"query": "{ ok }"}}])

    assert websocket.closed == (4400, "Invalid message")


def test_a_complete_without_an_id_closes_with_4400() -> None:
    websocket = _run([{"type": "connection_init"}, {"type": "complete"}])

    assert websocket.closed == (4400, "Invalid message")


def test_a_subscribe_without_a_payload_closes_with_4400() -> None:
    websocket = _run([{"type": "connection_init"}, {"type": "subscribe", "id": "1"}])

    assert websocket.closed == (4400, "Invalid message")


def test_an_unknown_message_type_closes_with_4400() -> None:
    websocket = _run([{"type": "connection_init"}, {"type": "nonsense"}])

    assert websocket.closed is not None
    assert websocket.closed[0] == 4400


def test_a_query_operation_produces_next_then_complete() -> None:
    websocket = _run(
        [{"type": "connection_init"}, {"type": "subscribe", "id": "1", "payload": {"query": "{ ok }"}}]
    )

    assert websocket.messages_of_type("next") == [{"type": "next", "id": "1", "payload": {"data": {"ok": True}}}]
    assert websocket.messages_of_type("complete") == [{"type": "complete", "id": "1"}]


def test_operation_teardown_completes_before_the_socket_closes() -> None:
    """`Task.cancel()` only *schedules* cancellation. Returning without awaiting left a
    subscription resolver's `finally` (and any generator-based `Depends` teardown) to run after the
    socket had already closed, or not before the process moved on at all.
    """
    _TORN_DOWN.clear()
    websocket = _FakeWebSocket(
        [
            {"type": "connection_init"},
            {"type": "subscribe", "id": "1", "payload": {"query": "subscription { ticks }"}},
        ]
    )

    asyncio.run(GraphQLTransportWSHandler(schema=_schema(), websocket=websocket).handle())

    assert _TORN_DOWN == ["ticks"], "the subscription generator must be torn down by the time handle() returns"


def test_a_client_complete_tears_the_operation_down_before_moving_on() -> None:
    _TORN_DOWN.clear()
    websocket = _FakeWebSocket(
        [
            {"type": "connection_init"},
            {"type": "subscribe", "id": "1", "payload": {"query": "subscription { ticks }"}},
            {"type": "complete", "id": "1"},
        ]
    )

    asyncio.run(GraphQLTransportWSHandler(schema=_schema(), websocket=websocket).handle())

    assert _TORN_DOWN == ["ticks"]


def test_a_handler_bug_is_logged_rather_than_silently_swallowed(caplog: Any) -> None:
    """The blanket `except Exception: pass` made a real defect look exactly like a client hanging
    up. A genuine failure now closes the socket *and* leaves a traceback behind.
    """

    class _BrokenWebSocket(_FakeWebSocket):
        async def send_json(self, data: Any) -> None:
            raise RuntimeError("simulated handler bug")

    websocket = _BrokenWebSocket([{"type": "connection_init"}])

    with caplog.at_level("ERROR"):
        asyncio.run(GraphQLTransportWSHandler(schema=_schema(), websocket=websocket).handle())

    assert "simulated handler bug" in caplog.text


def test_a_client_disconnect_is_not_logged_as_an_error(caplog: Any) -> None:
    # The counterpart: an ordinary disconnect must stay quiet, or the logs fill with noise.
    with caplog.at_level("ERROR"):
        _run([{"type": "connection_init"}])

    assert caplog.text == ""
