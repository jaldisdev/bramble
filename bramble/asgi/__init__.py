from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.types import Receive, Scope, Send
from starlette.websockets import WebSocket

from bramble.http.async_base_view import AsyncBaseHTTPView
from bramble.http.exceptions import HTTPException
from bramble.subscriptions import GraphQLTransportWSHandler

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


class GraphQL(AsyncBaseHTTPView[Request, Response]):
    """A plain ASGI application serving `schema` over HTTP (GET/POST, JSON and multipart bodies,
    batching if `schema.config.batching_config` enables it, the GraphiQL IDE on a bare browser
    `GET`) and WebSocket (the `graphql-transport-ws` subscription protocol). Usable directly as a
    Starlette route target, or mounted at the root of its own minimal app -- see `bramble.cli`'s
    dev server for exactly that.
    """

    def __init__(self, schema: "Schema", *, multipart_uploads_enabled: bool = True) -> None:
        self.schema = schema
        self.multipart_uploads_enabled = multipart_uploads_enabled

    async def get_body(self, request: Request) -> bytes:
        return await request.body()

    async def get_form_data(self, request: Request) -> Mapping[str, Any]:
        form = await request.form()
        return dict(form)

    async def get_context(self, request: Request) -> Any:
        return {"request": request}

    def create_response(self, response_data: Any) -> Response:
        return JSONResponse(response_data)

    def create_html_response(self, html: str) -> Response:
        return HTMLResponse(html)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "websocket":
            websocket = WebSocket(scope, receive=receive, send=send)
            handler = GraphQLTransportWSHandler(schema=self.schema, websocket=websocket)
            await handler.handle()
            return

        request = Request(scope, receive=receive, send=send)
        protocol_request = _StarletteRequestAdapter(request)
        try:
            response = await self.run(request, protocol_request)
        except HTTPException as error:
            response = JSONResponse({"errors": [{"message": error.message}]}, status_code=error.status_code)
        await response(scope, receive, send)
