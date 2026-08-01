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
from collections.abc import AsyncIterator, Mapping
from typing import TYPE_CHECKING, Any, Generic, TypeGuard, TypeVar

from bramble._bramble import lower_query
from bramble._error import GraphQLError, error_to_dict
from bramble._execution import _has_incremental_markers
from bramble.http.base import BaseRequestProtocol, BaseView
from bramble.http.exceptions import HTTPException
from bramble.http.multipart import MULTIPART_MIXED_BOUNDARY, encode_multipart_stream
from bramble.http.parse_content_type import parse_content_type
from bramble.http.types import GraphQLRequestData
from bramble.subscriptions.graphql_transport_ws import GRAPHQL_TRANSPORT_WS_PROTOCOL, GraphQLTransportWSHandler

if TYPE_CHECKING:
    from bramble._schema import Schema

Request = TypeVar("Request")
Response = TypeVar("Response")
WebSocketRequest = TypeVar("WebSocketRequest")
WebSocketResponse = TypeVar("WebSocketResponse")


class AsyncBaseHTTPView(
    BaseView[Request], Generic[Request, Response, WebSocketRequest, WebSocketResponse]
):
    """The async request/response cycle every concrete adapter (Starlette, raw ASGI, FastAPI,
    Flask, Django/Channels) drives: parse whatever shape the request came in as (GET query
    params, a single JSON body, a batched JSON array, or a multipart file-upload request) into one
    or more `GraphQLRequestData`, execute each against `self.schema`, and hand the result(s) back
    through `create_response`/`create_html_response` for the concrete adapter to turn into its own
    framework's actual response type. WebSocket requests are dispatched through the same `run()`
    entry point (see `is_websocket_request`/`pick_websocket_subprotocol`/
    `create_websocket_response` below) so a concrete adapter implements one hook set for both
    transports instead of re-deriving its own branch-and-delegate dance.

    Every method below that isn't already implemented is exactly what a concrete adapter must
    supply -- reading the raw body, parsing multipart form fields, building the resolver context/
    root value, and constructing a real response/websocket object are all inherently
    framework-specific.
    """

    schema: "Schema"
    graphql_transport_ws_handler_class: type[GraphQLTransportWSHandler] = GraphQLTransportWSHandler

    async def get_body(self, request: Request) -> bytes:
        raise NotImplementedError

    async def get_form_data(self, request: Request) -> Mapping[str, Any]:
        raise NotImplementedError

    async def get_context(self, request: Request | WebSocketRequest) -> Any:
        raise NotImplementedError

    async def get_root_value(self, request: Request | WebSocketRequest) -> Any:
        return None

    def create_response(self, response_data: dict[str, Any] | list[dict[str, Any]]) -> Response:
        raise NotImplementedError

    def create_html_response(self, html: str) -> Response:
        raise NotImplementedError

    async def create_multipart_response(self, stream: AsyncIterator[bytes]) -> Response:
        """Builds a `multipart/mixed` streaming response (§ incremental delivery) from
        `stream`'s already-framed bytes (`bramble.http.multipart.encode_multipart_stream`'s own
        output) -- only ever called for a single (non-batch), `@defer`/`@stream`-using POST
        operation; a concrete adapter that can't stream a chunked response (none currently can't --
        see each adapter's own module for how it does) would raise here instead.
        `bramble.http.multipart.multipart_content_type()` gives the matching top-level
        `Content-Type` header value to set on whatever response object this constructs.
        """
        raise NotImplementedError

    def is_websocket_request(self, request: Request | WebSocketRequest) -> TypeGuard[WebSocketRequest]:
        """Distinguishes a WebSocket handshake request from a plain HTTP one -- a `TypeGuard` so
        the rest of `run()`'s websocket branch can treat `request` as a `WebSocketRequest` without
        an explicit cast.
        """
        raise NotImplementedError

    async def pick_websocket_subprotocol(self, request: WebSocketRequest) -> str | None:
        """Returns the one WebSocket subprotocol bramble speaks (`graphql-transport-ws`) if the
        client offered it, else `None` -- bramble deliberately doesn't implement the legacy
        `graphql-ws` subprotocol, so there's nothing else to negotiate.
        """
        raise NotImplementedError

    async def create_websocket_response(self, request: WebSocketRequest, subprotocol: str | None) -> WebSocketResponse:
        """Wraps the raw framework request into whatever object satisfies
        `bramble.subscriptions.graphql_transport_ws.WebSocketProtocol`. Must NOT accept the
        connection itself -- `GraphQLTransportWSHandler.handle()` calls `accept(subprotocol=...)`
        exactly once, so accepting here too would double-accept.
        """
        raise NotImplementedError

    def _max_batch_operations(self) -> int | None:
        batching_config = self.schema.config.batching_config
        return batching_config["max_operations"] if batching_config is not None else None

    async def _parse_post_body(
        self, request: Request, protocol_request: BaseRequestProtocol
    ) -> list[GraphQLRequestData]:
        content_type, _ = parse_content_type(protocol_request.headers.get("content-type", ""))

        if content_type == "multipart/form-data":
            if not self.multipart_uploads_enabled:
                raise HTTPException(400, "File uploads are not enabled for this schema")
            form = await self.get_form_data(request)
            return self.parse_multipart_operations(form)

        body = await self.get_body(request)
        if not body:
            raise HTTPException(400, "No GraphQL query found in the request")
        parsed = self.parse_json(body)

        if isinstance(parsed, list):
            return self.parse_batch(parsed, max_operations=self._max_batch_operations())
        return [self.request_data_from_dict(parsed)]

    async def _execute(self, request_data: GraphQLRequestData, context: Any, root_value: Any) -> dict[str, Any]:
        if request_data.query is None:
            raise HTTPException(400, "No GraphQL query found in the request")
        try:
            return await self.schema.execute_async(
                request_data.query,
                variable_values=request_data.variables,
                context=context,
                root_value=root_value,
                operation_name=request_data.operation_name,
            )
        except GraphQLError as error:
            # A request-level failure (malformed query, unknown operation, ...) raised directly
            # by `execute_async` rather than returned inside a successful response's own
            # `errors` list -- still rendered in the same spec shape, just as the whole response.
            return {"data": None, "errors": [error_to_dict(error)]}

    def _needs_incremental_delivery(self, request_data: GraphQLRequestData) -> bool:
        """A cheap lowering peek to decide whether this single operation uses `@defer`/`@stream`
        at all -- mirrors `GraphQLTransportWSHandler._run_operation`'s identical "peek, then let
        the real call re-lower properly" pattern (`bramble/subscriptions/graphql_transport_ws.py`).
        A malformed query is deliberately not raised here -- `_execute`'s own real
        `schema.execute_async` call surfaces that error in the normal (non-streamed) response
        shape, which is simpler for a client to handle than a malformed query arriving as a
        `multipart/mixed` stream.
        """
        if request_data.query is None:
            return False
        try:
            _, fields = lower_query(
                request_data.query,
                variable_values=request_data.variables or {},
                operation_name=request_data.operation_name,
            )
        except GraphQLError:
            return False
        return _has_incremental_markers(fields)

    async def _stream_incremental(
        self, request_data: GraphQLRequestData, context: Any, root_value: Any
    ) -> AsyncIterator[dict[str, Any]]:
        assert request_data.query is not None
        async for payload in self.schema.execute_incremental(
            request_data.query,
            variable_values=request_data.variables,
            context=context,
            root_value=root_value,
            operation_name=request_data.operation_name,
        ):
            yield payload

    async def _run_websocket(self, request: WebSocketRequest) -> WebSocketResponse:
        subprotocol = await self.pick_websocket_subprotocol(request)
        websocket = await self.create_websocket_response(request, subprotocol)

        if subprotocol != GRAPHQL_TRANSPORT_WS_PROTOCOL:
            await websocket.close(code=4406, reason="Subprotocol not acceptable")  # type: ignore[attr-defined]
            return websocket

        await self.graphql_transport_ws_handler_class(schema=self.schema, websocket=websocket).handle()  # type: ignore[arg-type]
        return websocket

    async def run(
        self, request: Request | WebSocketRequest, protocol_request: BaseRequestProtocol | None = None
    ) -> Response | WebSocketResponse:
        if self.is_websocket_request(request):
            return await self._run_websocket(request)

        assert protocol_request is not None

        if not self.is_request_allowed(protocol_request):
            raise HTTPException(405, "GraphQL only supports GET and POST requests")

        if self.should_render_graphql_ide(protocol_request):
            return self.create_html_response(self.graphql_ide_html)

        if protocol_request.method == "GET":
            request_data_list = [self.request_data_from_dict(self.parse_query_params(protocol_request.query_params))]
        else:
            request_data_list = await self._parse_post_body(request, protocol_request)

        context = await self.get_context(request)
        root_value = await self.get_root_value(request)

        if len(request_data_list) > 1:
            results = await asyncio.gather(
                *(self._execute(request_data, context, root_value) for request_data in request_data_list)
            )
            return self.create_response(list(results))

        request_data = request_data_list[0]

        # `@defer`/`@stream` delivery is `multipart/mixed` over POST only (§ incremental delivery
        # scope notes) -- a single, non-batched operation, never a GET query or a batch entry (the
        # reference incremental-delivery spec itself only ever streams a single operation's own
        # response, not a batch of them).
        if protocol_request.method == "POST" and self._needs_incremental_delivery(request_data):
            stream = encode_multipart_stream(self._stream_incremental(request_data, context, root_value))
            return await self.create_multipart_response(stream)

        result = await self._execute(request_data, context, root_value)
        return self.create_response(result)
