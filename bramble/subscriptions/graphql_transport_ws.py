from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Protocol

from bramble._bramble import lower_query
from bramble._error import GraphQLError, error_to_dict

if TYPE_CHECKING:
    from bramble._schema import Schema

GRAPHQL_TRANSPORT_WS_PROTOCOL = "graphql-transport-ws"


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
        except Exception:  # noqa: BLE001 -- any receive/protocol failure just ends the socket cleanly.
            pass
        finally:
            for task in self.operations.values():
                task.cancel()
            try:
                await self.websocket.close()
            except Exception:  # noqa: BLE001 -- already closing; nothing meaningful to do with this.
                pass

    async def _handle_message(self, message: dict[str, Any]) -> None:
        message_type = message.get("type")

        if message_type == "connection_init":
            self.connection_initialised = True
            self.connection_params = message.get("payload")
            await self.websocket.send_json({"type": "connection_ack"})
            return

        if not self.connection_initialised:
            await self.websocket.close(code=4401, reason="Unauthorized")
            return

        if message_type == "subscribe":
            operation_id = message["id"]
            self.operations[operation_id] = asyncio.create_task(self._run_operation(operation_id, message["payload"]))
            return

        if message_type == "complete":
            task = self.operations.pop(message["id"], None)
            if task is not None:
                task.cancel()
            return

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
