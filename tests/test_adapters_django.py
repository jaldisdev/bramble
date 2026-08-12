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
import dataclasses
import datetime
import io
import json
from collections.abc import AsyncGenerator, Callable
from typing import Any

import django
import pytest
from asgiref.sync import iscoroutinefunction
from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator
from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.http import HttpRequest
from django.test import RequestFactory
from django.views.decorators.csrf import csrf_exempt

if not settings.configured:
    # An in-memory channel layer so the `listen_to_channel` tests below have something to join
    # groups on -- Channels resolves the layer from settings at connect time, and the same
    # `get_channel_layer()` singleton is what a test's own `group_send` reaches.
    settings.configure(
        DEBUG=True,
        ALLOWED_HOSTS=["*"],
        CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}},
    )
    django.setup()

import bramble
from bramble.adapters.django.channels import GraphQLWSConsumer
from bramble.adapters.django.views import AsyncGraphQLView, graphql_view
from bramble.http.types import TemporalResponse
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


# The view's extension points: a custom context, a status code set from inside execution, a JSON
# encoder, and the GraphiQL toggle. Everything below drives `AsyncGraphQLView` (or a subclass of it)
# rather than `graphql_view`, since that is what an application overriding a hook actually mounts.


@dataclasses.dataclass
class _Context:
    request: HttpRequest
    response: TemporalResponse
    account: str


@bramble.type
class _ContextQuery:
    @bramble.field
    def account(info: bramble.Info) -> str:
        return info.context.account

    @bramble.field
    def method(info: bramble.Info) -> str:
        return info.context.request.method

    @bramble.field
    def deny(info: bramble.Info) -> str | None:
        info.context.response.status_code = 401
        return None

    @bramble.field
    def stamp(info: bramble.Info) -> str:
        info.context.response.headers["X-Stamp"] = "set-during-execution"
        return "stamped"

    @bramble.field
    def when() -> datetime.datetime:
        return datetime.datetime(2026, 8, 12, 9, 30, tzinfo=datetime.timezone.utc)


class _ContextView(AsyncGraphQLView):
    async def get_context(self, request: HttpRequest) -> _Context:
        return _Context(request=request, response=self.sub_response, account="acct-1")


def _post(view: Callable[..., Any], query: str) -> Any:
    request = RequestFactory().post("/graphql", data={"query": query}, content_type="application/json")
    return asyncio.run(view(request))


def test_a_subclass_can_supply_its_own_context_object() -> None:
    view = _ContextView.as_view(schema=bramble.Schema(query=_ContextQuery))

    response = _post(view, "{ account method }")

    assert json.loads(response.content) == {"data": {"account": "acct-1", "method": "POST"}}


def test_a_resolver_can_set_the_response_status_code_during_execution() -> None:
    view = _ContextView.as_view(schema=bramble.Schema(query=_ContextQuery))

    response = _post(view, "{ deny }")

    assert response.status_code == 401
    assert json.loads(response.content) == {"data": {"deny": None}}


def test_a_resolver_can_set_a_response_header_during_execution() -> None:
    view = _ContextView.as_view(schema=bramble.Schema(query=_ContextQuery))

    response = _post(view, "{ stamp }")

    assert response["X-Stamp"] == "set-during-execution"


def test_the_response_stub_does_not_leak_between_requests() -> None:
    """`as_view` builds a view per request, which is what makes `self.sub_response` safe to mutate.
    A shared instance would carry the 401 from one request into the next.
    """
    view = _ContextView.as_view(schema=bramble.Schema(query=_ContextQuery))

    assert _post(view, "{ deny }").status_code == 401
    assert _post(view, "{ account }").status_code == 200


def test_the_json_encoder_is_overridable() -> None:
    class _ReadableJSONEncoder(DjangoJSONEncoder):
        def default(self, o: Any) -> Any:
            if isinstance(o, datetime.datetime):
                return o.strftime("%d %B %Y")
            return super().default(o)

    schema = bramble.Schema(
        query=_ContextQuery,
        config=SchemaConfig(scalar_map={datetime.datetime: bramble.scalar(name="DateTime", serialize=lambda value: value)}),
    )
    view = _ContextView.as_view(schema=schema, json_encoder=_ReadableJSONEncoder)

    response = _post(view, "{ when }")

    assert json.loads(response.content) == {"data": {"when": "12 August 2026"}}


def test_graphiql_is_served_on_a_browser_get_by_default() -> None:
    view = graphql_view(bramble.Schema(query=_Query))
    request = RequestFactory().get("/graphql", headers={"accept": "text/html"})

    response = asyncio.run(view(request))

    assert response.status_code == 200
    assert response["content-type"] == "text/html; charset=utf-8"
    assert b"graphiql" in response.content.lower()


def test_graphql_ide_false_does_not_serve_graphiql() -> None:
    view = graphql_view(bramble.Schema(query=_Query), graphql_ide=False)
    request = RequestFactory().get("/graphql", headers={"accept": "text/html"})

    response = asyncio.run(view(request))

    assert response.status_code == 400
    assert json.loads(response.content) == {"errors": [{"message": "No GraphQL query found in the request"}]}


def test_graphql_ide_false_still_answers_a_get_query() -> None:
    view = graphql_view(bramble.Schema(query=_Query), graphql_ide=False)
    request = RequestFactory().get("/graphql", {"query": "{ greet }"}, headers={"accept": "text/html"})

    response = asyncio.run(view(request))

    assert json.loads(response.content) == {"data": {"greet": "Hello, world!"}}


def test_as_view_returns_a_coroutine_function() -> None:
    assert iscoroutinefunction(AsyncGraphQLView.as_view(schema=bramble.Schema(query=_Query)))


def test_a_decorated_view_still_dispatches() -> None:
    """The decorator stack a Django app wraps its endpoint in (`csrf_exempt`, auth, rate limiting)
    applies to what `as_view()` returns, not to a Django `View` subclass.
    """
    view = csrf_exempt(AsyncGraphQLView.as_view(schema=bramble.Schema(query=_Query)))

    response = _post(view, "{ greet }")

    assert json.loads(response.content) == {"data": {"greet": "Hello, world!"}}


# The Channels consumer's own extension points: a connection-scoped context, and the channel-layer
# helpers a subscription backed by `group_send` needs. A producer elsewhere in an application sends
# `{"type": "live.cart", ...}` to a group; `listen_to_channel` is how a resolver receives it.

# Created inside each scenario's own loop rather than at import: an `asyncio.Event` binds to the
# first loop that waits on it, and every test here runs its own `asyncio.run`.
_LISTENING: dict[str, asyncio.Event] = {}


@bramble.type
class _ChannelSubscription:
    @bramble.subscription
    async def cart(info: bramble.Info) -> AsyncGenerator[str, None]:
        websocket = info.context["ws"]
        try:
            async with websocket.listen_to_channel("live.cart", groups=["cart.1"]) as messages:
                _LISTENING["cart"].set()
                async for message in messages:
                    yield message["payload"]
        finally:
            # Set *after* the context manager has unwound, so a test awaiting it knows the group
            # has already been discarded rather than racing the teardown.
            _LISTENING["closed"].set()

    @bramble.subscription
    async def account(info: bramble.Info) -> AsyncGenerator[str, None]:
        yield info.context["account"]


class _ContextConsumer(GraphQLWSConsumer):
    async def get_context(self, connection_params: Any) -> Any:
        return {"account": f"{self.scope['path']}:{connection_params['token']}"}


async def _subscribe(communicator: WebsocketCommunicator, query: str) -> None:
    await communicator.send_json_to({"type": "subscribe", "id": "1", "payload": {"query": query}})


def test_listen_to_channel_delivers_group_messages_to_a_subscription() -> None:
    schema = bramble.Schema(query=_Query, subscription=_ChannelSubscription)
    app = GraphQLWSConsumer.as_asgi(schema=schema)

    async def scenario() -> list[Any]:
        _LISTENING["cart"] = asyncio.Event()
        _LISTENING["closed"] = asyncio.Event()
        communicator = WebsocketCommunicator(app, "/graphql", subprotocols=["graphql-transport-ws"])
        connected, _ = await communicator.connect()
        assert connected
        await communicator.send_json_to({"type": "connection_init"})
        assert await communicator.receive_json_from() == {"type": "connection_ack"}

        await _subscribe(communicator, "subscription { cart }")
        # The group is only joined once the resolver reaches its `async with`; sending before that
        # would race the subscription rather than test it.
        await asyncio.wait_for(_LISTENING["cart"].wait(), timeout=5)

        channel_layer = get_channel_layer()
        await channel_layer.group_send("cart.1", {"type": "live.cart", "payload": "one"})
        await channel_layer.group_send("cart.1", {"type": "live.cart", "payload": "two"})

        received = [await communicator.receive_json_from(), await communicator.receive_json_from()]

        await communicator.send_json_to({"type": "complete", "id": "1"})
        await communicator.disconnect()
        return received

    assert asyncio.run(scenario()) == [
        {"type": "next", "id": "1", "payload": {"data": {"cart": "one"}}},
        {"type": "next", "id": "1", "payload": {"data": {"cart": "two"}}},
    ]


def test_the_group_is_discarded_when_the_client_unsubscribes() -> None:
    schema = bramble.Schema(query=_Query, subscription=_ChannelSubscription)
    app = GraphQLWSConsumer.as_asgi(schema=schema)

    async def scenario() -> Any:
        _LISTENING["cart"] = asyncio.Event()
        _LISTENING["closed"] = asyncio.Event()
        communicator = WebsocketCommunicator(app, "/graphql", subprotocols=["graphql-transport-ws"])
        await communicator.connect()
        await communicator.send_json_to({"type": "connection_init"})
        await communicator.receive_json_from()

        await _subscribe(communicator, "subscription { cart }")
        await asyncio.wait_for(_LISTENING["cart"].wait(), timeout=5)
        channel_layer = get_channel_layer()
        assert channel_layer.groups.get("cart.1")

        # A client-sent `complete` is acknowledged by the operation simply stopping -- the protocol
        # has the server send `complete` only when the *stream* ends on its own.
        await communicator.send_json_to({"type": "complete", "id": "1"})
        await asyncio.wait_for(_LISTENING["closed"].wait(), timeout=5)
        remaining = channel_layer.groups.get("cart.1")

        await communicator.disconnect()
        return remaining

    assert not asyncio.run(scenario())


def test_get_context_sees_the_scope_and_the_connection_init_payload() -> None:
    schema = bramble.Schema(query=_Query, subscription=_ChannelSubscription)
    app = _ContextConsumer.as_asgi(schema=schema)

    async def scenario() -> Any:
        communicator = WebsocketCommunicator(app, "/graphql", subprotocols=["graphql-transport-ws"])
        await communicator.connect()
        await communicator.send_json_to({"type": "connection_init", "payload": {"token": "t0ken"}})
        assert await communicator.receive_json_from() == {"type": "connection_ack"}

        await _subscribe(communicator, "subscription { account }")
        message = await communicator.receive_json_from()
        await communicator.disconnect()
        return message

    assert asyncio.run(scenario()) == {
        "type": "next",
        "id": "1",
        "payload": {"data": {"account": "/graphql:t0ken"}},
    }


def test_the_default_context_exposes_the_consumer_itself() -> None:
    schema = bramble.Schema(query=_Query, subscription=_ChannelSubscription)
    consumer = GraphQLWSConsumer(schema=schema)
    consumer.scope = {"path": "/graphql"}

    context = asyncio.run(consumer.get_context({"token": "t0ken"}))

    assert context["ws"] is consumer
    assert context["scope"] == {"path": "/graphql"}
    assert context["connection_params"] == {"token": "t0ken"}


def test_listen_to_channel_without_a_channel_layer_raises() -> None:
    consumer = GraphQLWSConsumer(schema=bramble.Schema(query=_Query))

    async def scenario() -> None:
        async with consumer.listen_to_channel("live.cart", groups=["cart.1"]):
            pass  # unreachable: entering the context manager is what raises

    with pytest.raises(RuntimeError, match="CHANNEL_LAYERS"):
        asyncio.run(scenario())


def test_a_schema_extension_can_map_errors_onto_the_status_code() -> None:
    """A `SchemaExtension` sees the whole operation, so the status code can be decided from the
    errors it produced rather than field by field -- the shape an application-wide
    error-code-to-HTTP-status mapping takes.
    """

    class UpdateStatusCode(bramble.SchemaExtension):
        def on_operation(self) -> Any:
            yield
            for error in self.execution_context.errors or ():
                if (error.extensions or {}).get("reason") == "forbidden":
                    self.execution_context.context.response.status_code = 403

    @bramble.type
    class Query:
        @bramble.field
        def secret() -> str | None:
            raise bramble.GraphQLError(
                "nope", code=bramble.ErrorCode.FIELD_RESOLUTION_FAILED, extensions={"reason": "forbidden"}
            )

    view = _ContextView.as_view(schema=bramble.Schema(query=Query, extensions=[UpdateStatusCode]))

    response = _post(view, "{ secret }")

    assert response.status_code == 403
    assert json.loads(response.content)["data"] == {"secret": None}
