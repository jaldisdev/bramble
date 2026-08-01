from __future__ import annotations

import asyncio
import io
import json
from collections.abc import AsyncGenerator

import django
from asgiref.sync import iscoroutinefunction
from channels.testing import WebsocketCommunicator
from django.conf import settings
from django.test import RequestFactory

if not settings.configured:
    settings.configure(DEBUG=True, ALLOWED_HOSTS=["*"])
    django.setup()

import bramble
from bramble.adapters.django.channels import GraphQLWSConsumer
from bramble.adapters.django.views import graphql_view
from bramble.schema.config import SchemaConfig

# Django's own `RequestFactory` builds requests without needing a full project/URLconf --
# `graphql_view(schema)` is called directly, mirroring how `tests/test_http.py` exercises
# `AsyncBaseHTTPView` directly rather than through a real running server.


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


def test_view_is_a_coroutine_function() -> None:
    schema = bramble.Schema(query=_Query)
    view = graphql_view(schema)

    assert iscoroutinefunction(view)


def test_get_with_query_param_executes() -> None:
    schema = bramble.Schema(query=_Query)
    view = graphql_view(schema)
    factory = RequestFactory()
    request = factory.get("/graphql", {"query": "{ greet }"})

    response = asyncio.run(view(request))

    assert response.status_code == 200
    assert json.loads(response.content) == {"data": {"greet": "Hello, world!"}}


def test_post_json_executes() -> None:
    schema = bramble.Schema(query=_Query)
    view = graphql_view(schema)
    factory = RequestFactory()
    request = factory.post(
        "/graphql",
        data={"query": "query($n: String) { greet(name: $n) }", "variables": {"n": "Ada"}},
        content_type="application/json",
    )

    response = asyncio.run(view(request))

    assert response.status_code == 200
    assert json.loads(response.content) == {"data": {"greet": "Hello, Ada!"}}


def test_post_batched_json_executes_each_operation() -> None:
    schema = bramble.Schema(query=_Query, config=SchemaConfig(batching_config={"max_operations": 5}))
    view = graphql_view(schema)
    factory = RequestFactory()
    request = factory.post(
        "/graphql",
        data=[
            {"query": 'query { greet(name: "A") }'},
            {"query": 'query { greet(name: "B") }'},
        ],
        content_type="application/json",
    )

    response = asyncio.run(view(request))

    assert response.status_code == 200
    assert json.loads(response.content) == [
        {"data": {"greet": "Hello, A!"}},
        {"data": {"greet": "Hello, B!"}},
    ]


def test_post_multipart_upload_executes() -> None:
    schema = bramble.Schema(query=_Query)
    view = graphql_view(schema)
    factory = RequestFactory()
    request = factory.post(
        "/graphql",
        data={
            "operations": '{"query": "query($f: Upload!) { uploadSize(file: $f) }", "variables": {"f": null}}',
            "map": '{"0": ["variables.f"]}',
            "0": io.BytesIO(b"hello upload"),
        },
    )

    response = asyncio.run(view(request))

    assert response.status_code == 200
    assert json.loads(response.content) == {"data": {"uploadSize": len(b"hello upload")}}


def test_disallowed_method_returns_405() -> None:
    schema = bramble.Schema(query=_Query)
    view = graphql_view(schema)
    factory = RequestFactory()
    request = factory.delete("/graphql")

    response = asyncio.run(view(request))

    assert response.status_code == 405


def test_defer_query_streams_a_multipart_mixed_response() -> None:
    schema = bramble.Schema(query=_DeferrableQuery, types=[_Author])
    view = graphql_view(schema)
    factory = RequestFactory()
    request = factory.post(
        "/graphql",
        data={"query": "query { id ... @defer { author { name } } }"},
        content_type="application/json",
    )

    async def scenario() -> tuple[int, str, bytes]:
        response = await view(request)
        body = b"".join([chunk async for chunk in response])
        return response.status_code, response["content-type"], body

    status_code, content_type, body = asyncio.run(scenario())

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


def test_websocket_subscription_streams_events_then_completes() -> None:
    schema = bramble.Schema(query=_Query, subscription=_Subscription)
    app = GraphQLWSConsumer.as_asgi(schema=schema)

    async def scenario() -> None:
        communicator = WebsocketCommunicator(app, "/graphql", subprotocols=["graphql-transport-ws"])
        connected, subprotocol = await communicator.connect()
        assert connected
        assert subprotocol == "graphql-transport-ws"

        await communicator.send_json_to({"type": "connection_init"})
        assert await communicator.receive_json_from() == {"type": "connection_ack"}

        await communicator.send_json_to(
            {"type": "subscribe", "id": "1", "payload": {"query": "subscription { count(upto: 3) }"}}
        )

        for expected in range(3):
            message = await communicator.receive_json_from()
            assert message == {"type": "next", "id": "1", "payload": {"data": {"count": expected}}}

        assert await communicator.receive_json_from() == {"type": "complete", "id": "1"}
        await communicator.disconnect()

    asyncio.run(scenario())


def test_websocket_closes_with_4406_for_an_unsupported_subprotocol() -> None:
    schema = bramble.Schema(query=_Query, subscription=_Subscription)
    app = GraphQLWSConsumer.as_asgi(schema=schema)

    async def scenario() -> None:
        communicator = WebsocketCommunicator(app, "/graphql", subprotocols=["graphql-ws"])
        connected, _ = await communicator.connect()
        assert connected is False

    asyncio.run(scenario())
