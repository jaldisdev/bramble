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


class GraphQLWSConsumer(AsyncWebsocketConsumer):
    """A Channels websocket consumer serving `schema`'s subscriptions over
    `graphql-transport-ws`. Place it in a `ProtocolTypeRouter`'s `"websocket"` route via
    `GraphQLWSConsumer.as_asgi(schema=schema)` (see this module's own docstring).
    """

    def __init__(self, schema: "Schema", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.schema = schema
        self.incoming: asyncio.Queue[Any] = asyncio.Queue()
        self._run_task: asyncio.Task[None] | None = None

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
        await GraphQLTransportWSHandler(schema=self.schema, websocket=adapter).handle()


__all__ = ["GraphQLWSConsumer"]
