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
import logging
from typing import TYPE_CHECKING, Any, Protocol

from bramble._bramble import lower_query
from bramble._error import GraphQLError, error_to_dict

if TYPE_CHECKING:
    from bramble._schema import Schema

GRAPHQL_TRANSPORT_WS_PROTOCOL = "graphql-transport-ws"

logger = logging.getLogger(__name__)

# The close codes `graphql-transport-ws` defines for protocol violations. Clients distinguish these
# from ordinary disconnects, so closing with a bare 1000 (or just dropping the socket) leaves them
# unable to tell "you sent something invalid" from "the server went away".
CLOSE_BAD_REQUEST = 4400
CLOSE_UNAUTHORIZED = 4401
CLOSE_SUBSCRIBER_ALREADY_EXISTS = 4409
CLOSE_TOO_MANY_INIT_REQUESTS = 4429


class WebSocketProtocol(Protocol):
    """Whatever a concrete framework's own WebSocket object is, it satisfies this structurally --
    the same "small structural protocol, not inheritance" approach `bramble.http.BaseRequestProtocol`
    already takes for plain HTTP requests, so a future non-ASGI transport could reuse this handler
    unchanged.
    """

    async def accept(self, subprotocol: str | None = None) -> None: ...
    async def receive_json(self) -> Any: ...
    async def send_json(self, data: Any) -> None: ...
    async def close(self, code: int = 1000, reason: str | None = None) -> None: ...


class GraphQLTransportWSHandler:
    """Implements the `graphql-transport-ws` subprotocol -- the current standard WebSocket
    subscription transport, and what a fresh GraphQL IDE speaks by default (the legacy
    `graphql-ws` subprotocol is deliberately out of scope, per the CLI roadmap's own plan).

    A query/mutation operation sent over the socket still works (a single `next` then
    `complete`); a subscription operation streams one `next` message per source event via
    `Schema.subscribe_async`. Each operation runs as its own `asyncio.Task`, so multiple
    concurrent operations (including multiple long-running subscriptions) on one socket don't
    block each other, and a client's own `complete` message (or the socket disconnecting) cancels
    just that operation's task.
    """

    def __init__(self, schema: "Schema", websocket: WebSocketProtocol) -> None:
        self.schema = schema
        self.websocket = websocket
        self.connection_initialised = False
        self.connection_params: Any = None
        self.operations: dict[str, asyncio.Task[None]] = {}

    async def build_context(self) -> Any:
        """Overridable: the context every operation on this socket executes with (mirrors
        `AsyncBaseHTTPView.get_context`'s own role for plain HTTP requests). Default: the
        `connection_init` message's own payload alongside the websocket itself, so a resolver can
        read e.g. an auth token supplied at connection time -- a subclass can override this for
        anything richer (a per-connection database session, ...).
        """
        return {"connection_params": self.connection_params, "websocket": self.websocket}

    async def handle(self) -> None:
        await self.websocket.accept(subprotocol=GRAPHQL_TRANSPORT_WS_PROTOCOL)
        try:
            while True:
                message = await self.websocket.receive_json()
                await self._handle_message(message)
        except asyncio.CancelledError:
            raise
        except (ConnectionError, EOFError, OSError):
            # The socket went away mid-receive. Ordinary, and nothing to report.
            pass
        except Exception:
            # Anything else reaching here is a bug in message handling, not a disconnect. It still
            # has to end the socket cleanly, but silently swallowing it (as this used to) meant a
            # real defect looked exactly like a client hanging up.
            logger.exception("graphql-transport-ws handler failed; closing the socket")
        finally:
            await self._shutdown_operations()
            try:
                await self.websocket.close()
            except Exception:  # already closing; nothing meaningful to do with this.
                logger.debug("failed to close the websocket cleanly", exc_info=True)

    async def _shutdown_operations(self) -> None:
        """Cancels every in-flight operation *and waits for each to finish unwinding.*

        Awaiting matters: `Task.cancel()` only schedules the cancellation. Returning immediately
        left each operation's own teardown -- a subscription resolver's `finally`, and any
        generator-based `Depends` provider's cleanup (§3c) -- to run after the socket had already
        closed, or not before the process moved on at all.
        """
        tasks = list(self.operations.values())
        self.operations.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _close(self, code: int, reason: str) -> None:
        await self._shutdown_operations()
        await self.websocket.close(code=code, reason=reason)

    async def _handle_message(self, message: dict[str, Any]) -> None:
        if not isinstance(message, dict):
            await self._close(CLOSE_BAD_REQUEST, "Invalid message")
            return

        message_type = message.get("type")

        if message_type == "connection_init":
            if self.connection_initialised:
                await self._close(CLOSE_TOO_MANY_INIT_REQUESTS, "Too many initialisation requests")
                return
            self.connection_initialised = True
            self.connection_params = message.get("payload")
            await self.websocket.send_json({"type": "connection_ack"})
            return

        # `ping` is answerable at any time, including before `connection_init` -- it's a liveness
        # check, not an operation. A client using protocol-level keepalive gets no reply at all
        # without this and eventually times the connection out on its own.
        if message_type == "ping":
            payload = message.get("payload")
            pong: dict[str, Any] = {"type": "pong"}
            if payload is not None:
                pong["payload"] = payload
            await self.websocket.send_json(pong)
            return

        if message_type == "pong":
            # A reply to a ping bramble never sends, or an unsolicited keepalive. Both are legal
            # and require no response.
            return

        if not self.connection_initialised:
            await self._close(CLOSE_UNAUTHORIZED, "Unauthorized")
            return

        if message_type == "subscribe":
            operation_id = message.get("id")
            if not isinstance(operation_id, str):
                await self._close(CLOSE_BAD_REQUEST, "Invalid message")
                return
            if operation_id in self.operations:
                # Overwriting the entry would orphan the running task: nothing would ever cancel
                # it, and it would keep streaming into the socket under an id the client believes
                # it has just re-bound.
                await self._close(
                    CLOSE_SUBSCRIBER_ALREADY_EXISTS, f"Subscriber for {operation_id} already exists"
                )
                return
            payload = message.get("payload")
            if not isinstance(payload, dict):
                await self._close(CLOSE_BAD_REQUEST, "Invalid message")
                return
            self.operations[operation_id] = asyncio.create_task(self._run_operation(operation_id, payload))
            return

        if message_type == "complete":
            operation_id = message.get("id")
            if not isinstance(operation_id, str):
                await self._close(CLOSE_BAD_REQUEST, "Invalid message")
                return
            task = self.operations.pop(operation_id, None)
            if task is not None:
                task.cancel()
                # Awaited for the same reason `_shutdown_operations` does: the client asked this
                # operation to stop, so its teardown should have run by the time we move on.
                await asyncio.gather(task, return_exceptions=True)
            return

        await self._close(CLOSE_BAD_REQUEST, f"Unknown message type: {message_type!r}")

    async def _run_operation(self, operation_id: str, payload: dict[str, Any]) -> None:
        query = payload.get("query")
        variables = payload.get("variables") or {}
        operation_name = payload.get("operationName")

        if query is None:
            await self.websocket.send_json(
                {"type": "error", "id": operation_id, "payload": [{"message": "No GraphQL query found in the request"}]}
            )
            self.operations.pop(operation_id, None)
            return

        try:
            # A cheap peek at the operation's own type (query/mutation vs. subscription) to pick
            # which of `Schema.execute_async`/`subscribe_async` to call -- both already re-run
            # this same parsing internally (and re-validate for real) once actually called, so
            # this never skips or duplicates any real validation, it just decides which method
            # to dispatch to.
            operation_type, _ = lower_query(query, variable_values=variables, operation_name=operation_name)
        except GraphQLError as error:
            await self.websocket.send_json({"type": "error", "id": operation_id, "payload": [error_to_dict(error)]})
            self.operations.pop(operation_id, None)
            return

        context = await self.build_context()

        try:
            if operation_type == "subscription":
                async for response in self.schema.subscribe_async(
                    query, variable_values=variables, operation_name=operation_name, context=context
                ):
                    await self.websocket.send_json({"type": "next", "id": operation_id, "payload": response})
            else:
                response = await self.schema.execute_async(
                    query, variable_values=variables, operation_name=operation_name, context=context
                )
                await self.websocket.send_json({"type": "next", "id": operation_id, "payload": response})
        except asyncio.CancelledError:
            raise
        except GraphQLError as error:
            await self.websocket.send_json({"type": "error", "id": operation_id, "payload": [error_to_dict(error)]})
            return
        else:
            await self.websocket.send_json({"type": "complete", "id": operation_id})
        finally:
            self.operations.pop(operation_id, None)
