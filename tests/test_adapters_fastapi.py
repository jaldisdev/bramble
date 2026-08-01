from __future__ import annotations

from collections.abc import AsyncGenerator

from fastapi import FastAPI
from fastapi.testclient import TestClient

import bramble
from bramble.adapters.fastapi import GraphQLRouter

# `bramble.adapters.fastapi.GraphQLRouter` is a thin wrapper mounting
# `bramble.adapters.starlette.GraphQL` into an `APIRouter` -- these tests exercise it the way a
# real FastAPI app would, through `app.include_router(...)` and FastAPI's own `TestClient`.


@bramble.type
class _Query:
    @bramble.field
    def greet(name: str = "world") -> str:
        return f"Hello, {name}!"


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


def _make_client(schema: bramble.Schema) -> TestClient:
    app = FastAPI()
    app.include_router(GraphQLRouter(schema, path="/graphql"))
    return TestClient(app)


def test_get_with_query_param_executes() -> None:
    schema = bramble.Schema(query=_Query)
    client = _make_client(schema)

    response = client.get("/graphql", params={"query": "{ greet }"})

    assert response.status_code == 200
    assert response.json() == {"data": {"greet": "Hello, world!"}}


def test_post_json_executes() -> None:
    schema = bramble.Schema(query=_Query)
    client = _make_client(schema)

    response = client.post(
        "/graphql", json={"query": "query($n: String) { greet(name: $n) }", "variables": {"n": "Ada"}}
    )

    assert response.status_code == 200
    assert response.json() == {"data": {"greet": "Hello, Ada!"}}


def test_get_without_query_serves_graphiql() -> None:
    schema = bramble.Schema(query=_Query)
    client = _make_client(schema)

    response = client.get("/graphql", headers={"accept": "text/html"})

    assert response.status_code == 200
    assert "GraphiQL" in response.text


def test_websocket_subscription_streams_events_then_completes() -> None:
    schema = bramble.Schema(query=_Query, subscription=_Subscription)
    client = _make_client(schema)

    with client.websocket_connect("/graphql", subprotocols=["graphql-transport-ws"]) as websocket:
        websocket.send_json({"type": "connection_init"})
        assert websocket.receive_json() == {"type": "connection_ack"}

        websocket.send_json({"type": "subscribe", "id": "1", "payload": {"query": "subscription { count(upto: 3) }"}})

        for expected in range(3):
            message = websocket.receive_json()
            assert message == {"type": "next", "id": "1", "payload": {"data": {"count": expected}}}

        assert websocket.receive_json() == {"type": "complete", "id": "1"}


def test_disallowed_method_returns_405() -> None:
    schema = bramble.Schema(query=_Query)
    client = _make_client(schema)

    response = client.delete("/graphql")

    assert response.status_code == 405


def test_defer_query_streams_a_multipart_mixed_response() -> None:
    schema = bramble.Schema(query=_DeferrableQuery, types=[_Author])
    client = _make_client(schema)

    with client.stream(
        "POST", "/graphql", json={"query": "query { id ... @defer { author { name } } }"}
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"] == 'multipart/mixed; boundary="graphql"'
        body = response.read()

    assert b'{"data": {"id": "q1"}, "hasNext": true}' in body
    assert b'"author": {"name": "Ada"}' in body
    assert body.endswith(b"--graphql--\r\n")
