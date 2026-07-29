from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

import httpx
import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import bramble
from bramble.adapters.starlette import GraphQL
from bramble.schema.config import SchemaConfig

# Real HTTP requests (via `httpx.ASGITransport`, no actual socket) and real WebSocket sessions
# (via Starlette's own `TestClient`) driven against the concrete `bramble.adapters.starlette.GraphQL`
# view --
# `tests/test_http.py` already covers the framework-agnostic request-shape logic through a fake
# request, so these focus on things only the real ASGI adapter can exercise: the actual HTTP
# response objects, and the WebSocket subprotocol end to end.


@bramble.type
class _Query:
    @bramble.field
    def greet(name: str = "world") -> str:
        return f"Hello, {name}!"

    @bramble.field
    async def upload_size(file: bramble.Upload) -> int:
        # `Upload`'s resolved value is whatever object the transport actually put into
        # variable_values -- over ASGI/Starlette, a real `UploadFile`, not raw bytes (the
        # `NewType`'s own wrapped type is just a nominal marker, not a runtime guarantee).
        content = await file.read()  # type: ignore[attr-defined]
        return len(content)


@bramble.type
class _Subscription:
    @bramble.field
    async def count(upto: int) -> AsyncGenerator[int, None]:
        for i in range(upto):
            yield i


async def _run_http(schema: bramble.Schema, request_fn):  # type: ignore[no-untyped-def]
    app = GraphQL(schema)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await request_fn(client)


def test_get_without_query_serves_graphiql() -> None:
    schema = bramble.Schema(query=_Query)

    async def request(client: httpx.AsyncClient) -> httpx.Response:
        return await client.get("/graphql", headers={"accept": "text/html"})

    response = asyncio.run(_run_http(schema, request))

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "GraphiQL" in response.text


def test_get_with_query_param_executes() -> None:
    schema = bramble.Schema(query=_Query)

    async def request(client: httpx.AsyncClient) -> httpx.Response:
        return await client.get("/graphql", params={"query": "{ greet }"})

    response = asyncio.run(_run_http(schema, request))

    assert response.status_code == 200
    assert response.json() == {"data": {"greet": "Hello, world!"}}


def test_post_json_executes() -> None:
    schema = bramble.Schema(query=_Query)

    async def request(client: httpx.AsyncClient) -> httpx.Response:
        return await client.post(
            "/graphql", json={"query": "query($n: String) { greet(name: $n) }", "variables": {"n": "Ada"}}
        )

    response = asyncio.run(_run_http(schema, request))

    assert response.status_code == 200
    assert response.json() == {"data": {"greet": "Hello, Ada!"}}


def test_post_batched_json_executes_each_operation() -> None:
    schema = bramble.Schema(query=_Query, config=SchemaConfig(batching_config={"max_operations": 5}))

    async def request(client: httpx.AsyncClient) -> httpx.Response:
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

    async def request(client: httpx.AsyncClient) -> httpx.Response:
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

    async def request(client: httpx.AsyncClient) -> httpx.Response:
        return await client.delete("/graphql")

    response = asyncio.run(_run_http(schema, request))

    assert response.status_code == 405


def test_websocket_subscription_streams_events_then_completes() -> None:
    schema = bramble.Schema(query=_Query, subscription=_Subscription)
    app = GraphQL(schema)
    client = TestClient(app)

    with client.websocket_connect("/graphql", subprotocols=["graphql-transport-ws"]) as websocket:
        websocket.send_json({"type": "connection_init"})
        assert websocket.receive_json() == {"type": "connection_ack"}

        websocket.send_json({"type": "subscribe", "id": "1", "payload": {"query": "subscription { count(upto: 3) }"}})

        for expected in range(3):
            message = websocket.receive_json()
            assert message == {"type": "next", "id": "1", "payload": {"data": {"count": expected}}}

        assert websocket.receive_json() == {"type": "complete", "id": "1"}


def test_websocket_query_operation_sends_one_next_then_complete() -> None:
    schema = bramble.Schema(query=_Query, subscription=_Subscription)
    app = GraphQL(schema)
    client = TestClient(app)

    with client.websocket_connect("/graphql", subprotocols=["graphql-transport-ws"]) as websocket:
        websocket.send_json({"type": "connection_init"})
        websocket.receive_json()

        websocket.send_json({"type": "subscribe", "id": "1", "payload": {"query": "{ greet }"}})

        assert websocket.receive_json() == {"type": "next", "id": "1", "payload": {"data": {"greet": "Hello, world!"}}}
        assert websocket.receive_json() == {"type": "complete", "id": "1"}


def test_websocket_rejects_operations_before_connection_init() -> None:
    schema = bramble.Schema(query=_Query, subscription=_Subscription)
    app = GraphQL(schema)
    client = TestClient(app)

    with client.websocket_connect("/graphql", subprotocols=["graphql-transport-ws"]) as websocket:
        websocket.send_json({"type": "subscribe", "id": "1", "payload": {"query": "{ greet }"}})
        with pytest.raises(Exception):  # noqa: PT011 -- Starlette raises its own WebSocketDisconnect here.
            websocket.receive_json()


def test_websocket_closes_with_4406_for_an_unsupported_subprotocol() -> None:
    schema = bramble.Schema(query=_Query, subscription=_Subscription)
    app = GraphQL(schema)
    client = TestClient(app)

    # The rejection happens before `accept()`, so Starlette's test client raises
    # `WebSocketDisconnect` immediately on connect rather than on a later `receive_json()`.
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect("/graphql", subprotocols=["graphql-ws"]):
            pass
    assert excinfo.value.code == 4406
