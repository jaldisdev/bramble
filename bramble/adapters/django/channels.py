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

"""A Django Channels WebSocket consumer -- Django's own views (sync or async) can't hold a
long-lived duplex socket, so WebSocket subscriptions need a separate ASGI framework layered on top;
Channels is the standard choice, gated behind bramble's own `django` extra (which pulls in
`channels` directly, since HTTP + WebSocket support are both expected out of the box).

Wire it into a Channels `ProtocolTypeRouter`, e.g.:

    from channels.routing import ProtocolTypeRouter, URLRouter
    from django.urls import re_path
    from bramble.adapters.django.channels import GraphQLWSConsumer

    application = ProtocolTypeRouter({
        "websocket": URLRouter([re_path(r"^graphql$", GraphQLWSConsumer.as_asgi(schema=schema))]),
    })
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator, AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from channels.generic.websocket import AsyncWebsocketConsumer

from bramble.subscriptions import GRAPHQL_TRANSPORT_WS_PROTOCOL, GraphQLTransportWSHandler

if TYPE_CHECKING:
    from bramble._schema import Schema


class _ChannelsWebSocketAdapter:
    """Satisfies `bramble.subscriptions.graphql_transport_ws.WebSocketProtocol` by bridging into a
    `GraphQLWSConsumer`'s own push-based Channels callbacks (`receive`/`send`/`close`) -- the queue
    on the consumer is what turns those pushes back into the pull-based `receive_json()`
    `GraphQLTransportWSHandler` expects.
    """

    def __init__(self, consumer: "GraphQLWSConsumer") -> None:
        self._consumer = consumer

    async def accept(self, subprotocol: str | None = None) -> None:
        await self._consumer.accept(subprotocol=subprotocol)

    async def receive_json(self) -> Any:
        message = await self._consumer.incoming.get()
        if message is None:
            raise RuntimeError("WebSocket disconnected")
        return message

    async def send_json(self, data: Any) -> None:
        await self._consumer.send(text_data=json.dumps(data))

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        await self._consumer.close(code=code, reason=reason)


class _ConsumerTransportWSHandler(GraphQLTransportWSHandler):
    """Routes `build_context` back to the consumer, so a subclass overrides one method
    (`GraphQLWSConsumer.get_context`) rather than having to also supply a handler class.
    """

    def __init__(self, schema: "Schema", websocket: Any, consumer: "GraphQLWSConsumer") -> None:
        super().__init__(schema=schema, websocket=websocket)
        self._consumer = consumer

    async def build_context(self) -> Any:
        return await self._consumer.get_context(self.connection_params)


class GraphQLWSConsumer(AsyncWebsocketConsumer):
    """A Channels websocket consumer serving `schema`'s subscriptions over
    `graphql-transport-ws`. Place it in a `ProtocolTypeRouter`'s `"websocket"` route via
    `GraphQLWSConsumer.as_asgi(schema=schema)` (see this module's own docstring).

    Channels instantiates one consumer per connection, so `self` is connection-scoped: whatever
    `get_context` builds lives as long as the socket does, and `listen_to_channel` below is safe to
    call concurrently from several subscriptions on the same socket.
    """

    #: Set by Channels itself (`AsyncConsumer.__call__`) once the connection opens, from
    #: `settings.CHANNEL_LAYERS`. Both are `None`/absent when no channel layer is configured, which
    #: is fine for a schema whose subscriptions don't use one -- `listen_to_channel` is the only
    #: thing here that requires one, and it says so.
    channel_layer: Any
    channel_name: str

    graphql_transport_ws_handler_class: type[_ConsumerTransportWSHandler] = _ConsumerTransportWSHandler

    def __init__(self, schema: "Schema", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.schema = schema
        self.incoming: asyncio.Queue[Any] = asyncio.Queue()
        self._run_task: asyncio.Task[None] | None = None
        # Keyed by the channel-layer message `type` each listener asked for; a list because two
        # concurrent subscriptions on one socket may well listen for the same message type.
        self._listen_queues: dict[str, list[asyncio.Queue[dict[str, Any]]]] = {}

    async def get_context(self, connection_params: Any) -> Any:
        """The value resolvers see as `info.context` for every operation on this socket. Override to
        return your own object -- typically built from `self.scope` (where Channels' own middleware
        leaves the session, user, headers, and URL route) and from `connection_params`, the payload
        the client sent with `connection_init`:

            async def get_context(self, connection_params):
                return MyContext(user=self.scope["user"], ws=self, params=connection_params)

        The default includes `self` under `"ws"` so a subscription resolver can reach
        `listen_to_channel`/`channel_layer` without needing an override at all.
        """
        return {"scope": self.scope, "connection_params": connection_params, "ws": self}

    @asynccontextmanager
    async def listen_to_channel(
        self, message_type: str, *, groups: Sequence[str] = ()
    ) -> AsyncIterator[AsyncGenerator[dict[str, Any], None]]:
        """Subscribes this connection to `groups` on the channel layer and yields an async generator
        of the messages arriving with `{"type": message_type}`:

            @bramble.subscription
            async def cart(info: bramble.Info) -> AsyncGenerator[Cart, None]:
                websocket = info.context["ws"]
                async with websocket.listen_to_channel("live.cart", groups=[f"cart.{cart_id}"]) as messages:
                    async for message in messages:
                        yield Cart(**message["payload"])

        Group membership is added on entry and discarded on exit -- including when the client
        unsubscribes or the socket drops, since that cancels the resolver's task and unwinds this
        context manager with it. The generator never ends on its own; it yields until whoever is
        iterating it stops.

        Messages are matched on `type` alone, exactly as a producer elsewhere sends them
        (`channel_layer.group_send(group, {"type": "live.cart", ...})`) -- the whole message dict is
        yielded, so any payload keys the producer set come through untouched. A message whose type
        no listener asked for falls through to Channels' own handler dispatch, so ordinary consumer
        methods keep working alongside this.

        Requires a configured channel layer (`settings.CHANNEL_LAYERS`); without one there is
        nothing to subscribe to, and this raises rather than silently yielding a generator that
        would never produce anything.
        """
        if getattr(self, "channel_layer", None) is None:
            raise RuntimeError(
                "listen_to_channel() requires a channel layer -- configure settings.CHANNEL_LAYERS"
            )

        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._listen_queues.setdefault(message_type, []).append(queue)
        joined: list[str] = []

        async def messages() -> AsyncGenerator[dict[str, Any], None]:
            while True:
                yield await queue.get()

        generator = messages()
        try:
            for group in groups:
                await self.channel_layer.group_add(group, self.channel_name)
                # Tracked as we go rather than assumed from `groups`: if one `group_add` fails
                # partway through, the `finally` below must still discard the ones that succeeded.
                joined.append(group)
            yield generator
        finally:
            await generator.aclose()
            for group in joined:
                await self.channel_layer.group_discard(group, self.channel_name)
            listeners = self._listen_queues.get(message_type, [])
            if queue in listeners:
                listeners.remove(queue)
            if not listeners:
                self._listen_queues.pop(message_type, None)

    async def dispatch(self, message: dict[str, Any]) -> None:
        """Feeds a channel-layer message to whoever is listening for its type, falling back to
        Channels' own handler lookup.

        The fallback is what keeps `websocket.connect`/`receive`/`disconnect` (and any handler
        method a subclass adds) working: those are dispatched through here too. Without the
        listener branch, a custom message type would instead hit `AsyncConsumer.dispatch`'s
        "No handler for message type" error, which is why a group message has to be intercepted
        before it gets there.
        """
        listeners = self._listen_queues.get(message.get("type", ""))
        if listeners:
            for queue in listeners:
                queue.put_nowait(message)
            return
        await super().dispatch(message)

    async def connect(self) -> None:
        self._run_task = asyncio.create_task(self._run())

    async def receive(self, text_data: str | None = None, bytes_data: bytes | None = None) -> None:
        if text_data is not None:
            await self.incoming.put(json.loads(text_data))

    async def disconnect(self, code: int) -> None:
        await self.incoming.put(None)
        if self._run_task is not None:
            await self._run_task

    async def _run(self) -> None:
        adapter = _ChannelsWebSocketAdapter(self)
        offered = self.scope.get("subprotocols", [])
        if GRAPHQL_TRANSPORT_WS_PROTOCOL not in offered:
            await adapter.close(code=4406, reason="Subprotocol not acceptable")
            return
        await self.graphql_transport_ws_handler_class(schema=self.schema, websocket=adapter, consumer=self).handle()


__all__ = ["GraphQLWSConsumer"]
