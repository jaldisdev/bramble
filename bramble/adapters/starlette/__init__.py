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

from collections.abc import AsyncIterator, Mapping
from typing import TYPE_CHECKING, Any, TypeGuard

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from starlette.types import Receive, Scope, Send
from starlette.websockets import WebSocket

from bramble.http.async_base_view import AsyncBaseHTTPView
from bramble.http.exceptions import HTTPException
from bramble.http.multipart import multipart_content_type
from bramble.subscriptions import GRAPHQL_TRANSPORT_WS_PROTOCOL

if TYPE_CHECKING:
    from bramble._schema import Schema


class _StarletteRequestAdapter:
    """Satisfies `bramble.http.base.BaseRequestProtocol` structurally, over a real Starlette
    `Request` -- the one place this module depends on Starlette's own request shape, kept
    separate from `AsyncBaseHTTPView`'s own framework-agnostic logic.
    """

    def __init__(self, request: Request) -> None:
        self._request = request

    @property
    def query_params(self) -> Mapping[str, str]:
        return self._request.query_params

    @property
    def method(self) -> str:
        return self._request.method

    @property
    def headers(self) -> Mapping[str, str]:
        return self._request.headers


class GraphQL(AsyncBaseHTTPView[Request, Response, WebSocket, WebSocket]):
    """A plain ASGI application serving `schema` over HTTP (GET/POST, JSON and multipart bodies,
    batching if `schema.config.batching_config` enables it, the GraphiQL IDE on a bare browser
    `GET`) and WebSocket (the `graphql-transport-ws` subscription protocol). Usable directly as a
    Starlette route target, or mounted at the root of its own minimal app -- see `bramble.cli`'s
    dev server for exactly that.
    """

    def __init__(
        self, schema: "Schema", *, multipart_uploads_enabled: bool = True, graphql_ide: bool = True
    ) -> None:
        self.schema = schema
        self.multipart_uploads_enabled = multipart_uploads_enabled
        self.graphql_ide = graphql_ide

    async def get_body(self, request: Request) -> bytes:
        return await request.body()

    async def get_form_data(self, request: Request) -> Mapping[str, Any]:
        form = await request.form()
        return dict(form)

    async def get_context(self, request: Request | WebSocket) -> Any:
        return {"request": request}

    def create_response(self, response_data: Any) -> Response:
        return JSONResponse(response_data)

    def create_html_response(self, html: str) -> Response:
        return HTMLResponse(html)

    async def create_multipart_response(self, stream: AsyncIterator[bytes]) -> Response:
        return StreamingResponse(stream, media_type=multipart_content_type())

    def is_websocket_request(self, request: Request | WebSocket) -> TypeGuard[WebSocket]:
        return isinstance(request, WebSocket)

    async def pick_websocket_subprotocol(self, request: WebSocket) -> str | None:
        offered = request.scope.get("subprotocols", [])
        return GRAPHQL_TRANSPORT_WS_PROTOCOL if GRAPHQL_TRANSPORT_WS_PROTOCOL in offered else None

    async def create_websocket_response(self, request: WebSocket, subprotocol: str | None) -> WebSocket:
        return request

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "websocket":
            websocket = WebSocket(scope, receive=receive, send=send)
            await self.run(websocket)
            return

        request = Request(scope, receive=receive, send=send)
        protocol_request = _StarletteRequestAdapter(request)
        try:
            response = await self.run(request, protocol_request)
        except HTTPException as error:
            response = JSONResponse({"errors": [{"message": error.message}]}, status_code=error.status_code)
        await response(scope, receive, send)
