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
import json
from collections.abc import AsyncGenerator
from typing import Any

import httpx2

import bramble
from bramble.adapters.asgi import GraphQL
from bramble.schema.config import SchemaConfig

# Same test scenarios as `tests/test_adapters_starlette.py`, but against the dependency-free raw
# ASGI adapter -- HTTP via `httpx2.ASGITransport` (itself framework-agnostic, no Starlette
# involved), WebSocket via a small hand-rolled ASGI websocket session (`_WebSocketSession` below),
# since there's no Starlette `TestClient` to reuse here without pulling Starlette back in.


@bramble.type
class _Query:
    @bramble.field
    def greet(name: str = "world") -> str:
        return f"Hello, {name}!"

    @bramble.field
    async def upload_size(file: bramble.Upload) -> int:
        content = await file.read()  # type: ignore[attr-defined]
        return len(content)


@bramble.type
class _Subscription:
    @bramble.field
    async def count(upto: int) -> AsyncGenerator[int, None]:
        for i in range(upto):
            yield i


@bramble.type
class _Author:
    name: str


@bramble.type
class _DeferrableQuery:
    @bramble.field
    def id() -> str:
        return "q1"

    @bramble.field
    def author() -> _Author:
        return _Author(name="Ada")


async def _run_http(schema: bramble.Schema, request_fn):  # type: ignore[no-untyped-def]
    app = GraphQL(schema)
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        return await request_fn(client)


def test_get_without_query_serves_graphiql() -> None:
    schema = bramble.Schema(query=_Query)

    async def request(client: httpx2.AsyncClient) -> httpx2.Response:
        return await client.get("/graphql", headers={"accept": "text/html"})

    response = asyncio.run(_run_http(schema, request))

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "GraphiQL" in response.text


def test_get_with_query_param_executes() -> None:
    schema = bramble.Schema(query=_Query)

    async def request(client: httpx2.AsyncClient) -> httpx2.Response:
        return await client.get("/graphql", params={"query": "{ greet }"})

    response = asyncio.run(_run_http(schema, request))

    assert response.status_code == 200
    assert response.json() == {"data": {"greet": "Hello, world!"}}


def test_post_json_executes() -> None:
    schema = bramble.Schema(query=_Query)

    async def request(client: httpx2.AsyncClient) -> httpx2.Response:
        return await client.post(
            "/graphql", json={"query": "query($n: String) { greet(name: $n) }", "variables": {"n": "Ada"}}
        )

    response = asyncio.run(_run_http(schema, request))

    assert response.status_code == 200
    assert response.json() == {"data": {"greet": "Hello, Ada!"}}


def test_post_batched_json_executes_each_operation() -> None:
    schema = bramble.Schema(query=_Query, config=SchemaConfig(batching_config={"max_operations": 5}))

    async def request(client: httpx2.AsyncClient) -> httpx2.Response:
        return await client.post(
            "/graphql",
            json=[
                {"query": 'query { greet(name: "A") }'},
                {"query": 'query { greet(name: "B") }'},
            ],
        )

    response = asyncio.run(_run_http(schema, request))

    assert response.status_code == 200
    assert response.json() == [
        {"data": {"greet": "Hello, A!"}},
        {"data": {"greet": "Hello, B!"}},
    ]


def test_post_multipart_upload_executes() -> None:
    schema = bramble.Schema(query=_Query)

    async def request(client: httpx2.AsyncClient) -> httpx2.Response:
        return await client.post(
            "/graphql",
            data={
                "operations": '{"query": "query($f: Upload!) { uploadSize(file: $f) }", "variables": {"f": null}}',
                "map": '{"0": ["variables.f"]}',
            },
            files={"0": ("greeting.txt", b"hello upload")},
        )

    response = asyncio.run(_run_http(schema, request))

    assert response.status_code == 200
    assert response.json() == {"data": {"uploadSize": len(b"hello upload")}}


def test_disallowed_method_returns_405() -> None:
    schema = bramble.Schema(query=_Query)

    async def request(client: httpx2.AsyncClient) -> httpx2.Response:
        return await client.delete("/graphql")

    response = asyncio.run(_run_http(schema, request))

    assert response.status_code == 405


def test_defer_query_streams_a_multipart_mixed_response() -> None:
    schema = bramble.Schema(query=_DeferrableQuery, types=[_Author])

    async def request(client: httpx2.AsyncClient) -> tuple[int, str, bytes]:
        async with client.stream(
            "POST", "/graphql", json={"query": "query { id ... @defer { author { name } } }"}
        ) as response:
            return response.status_code, response.headers["content-type"], await response.aread()

    status_code, content_type, body = asyncio.run(_run_http(schema, request))

    assert status_code == 200
    assert content_type == 'multipart/mixed; boundary="graphql"'
    assert body == (
        b"--graphql\r\n"
        b"Content-Type: application/json; charset=utf-8\r\n"
        b"\r\n"
        b'{"data": {"id": "q1"}, "hasNext": true}\r\n'
        b"--graphql\r\n"
        b"Content-Type: application/json; charset=utf-8\r\n"
        b"\r\n"
        b'{"incremental": [{"data": {"author": {"name": "Ada"}}, "path": []}], "hasNext": false}\r\n'
        b"--graphql--\r\n"
    )


class _WebSocketSession:
    """Drives a raw ASGI websocket conversation against `app` directly -- no Starlette
    `TestClient` involved, since this adapter has no Starlette dependency to exercise one with.
    """

    def __init__(self, app: Any, subprotocols: list[str]) -> None:
        self._app = app
        self._scope = {"type": "websocket", "subprotocols": subprotocols}
        self._to_app: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._from_app: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None
        self.accepted = False
        self.handshake_message: dict[str, Any] = {}

    async def __aenter__(self) -> "_WebSocketSession":
        async def receive() -> dict[str, Any]:
            return await self._to_app.get()

        async def send(message: dict[str, Any]) -> None:
            await self._from_app.put(message)

        self._task = asyncio.create_task(self._app(self._scope, receive, send))
        await self._to_app.put({"type": "websocket.connect"})
        self.handshake_message = await self._from_app.get()
        assert self.handshake_message["type"] in ("websocket.accept", "websocket.close")
        self.accepted = self.handshake_message["type"] == "websocket.accept"
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()

    async def send_json(self, data: Any) -> None:
        await self._to_app.put({"type": "websocket.receive", "text": json.dumps(data)})

    async def receive_raw(self) -> dict[str, Any]:
        return await self._from_app.get()

    async def receive_json(self) -> Any:
        message = await self.receive_raw()
        assert message["type"] == "websocket.send"
        return json.loads(message["text"])


def test_websocket_subscription_streams_events_then_completes() -> None:
    schema = bramble.Schema(query=_Query, subscription=_Subscription)
    app = GraphQL(schema)

    async def scenario() -> None:
        async with _WebSocketSession(app, ["graphql-transport-ws"]) as websocket:
            await websocket.send_json({"type": "connection_init"})
            assert await websocket.receive_json() == {"type": "connection_ack"}

            await websocket.send_json(
                {"type": "subscribe", "id": "1", "payload": {"query": "subscription { count(upto: 3) }"}}
            )

            for expected in range(3):
                message = await websocket.receive_json()
                assert message == {"type": "next", "id": "1", "payload": {"data": {"count": expected}}}

            assert await websocket.receive_json() == {"type": "complete", "id": "1"}

    asyncio.run(scenario())


def test_websocket_closes_with_4406_for_an_unsupported_subprotocol() -> None:
    schema = bramble.Schema(query=_Query, subscription=_Subscription)
    app = GraphQL(schema)

    async def scenario() -> None:
        async with _WebSocketSession(app, ["graphql-ws"]) as websocket:
            assert websocket.accepted is False
            assert websocket.handshake_message == {
                "type": "websocket.close",
                "code": 4406,
                "reason": "Subprotocol not acceptable",
            }

    asyncio.run(scenario())
