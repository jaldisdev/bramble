"""A plain ASGI GraphQL view with **no framework dependency** beyond the ASGI spec itself -- no
Starlette/FastAPI import anywhere in this module, unlike `bramble.adapters.starlette` (which has
the exact same public shape, but is built on Starlette's own request/response/websocket types).
Pick this one to keep bramble's own footprint as the only new dependency; pick the Starlette one if
the host app already depends on Starlette anyway.
"""

from __future__ import annotations

import io
import json
from collections.abc import Awaitable, Callable, Mapping
from typing import TYPE_CHECKING, Any, TypeGuard
from urllib.parse import parse_qsl

from python_multipart.multipart import parse_form

from bramble.http.async_base_view import AsyncBaseHTTPView
from bramble.http.exceptions import HTTPException
from bramble.subscriptions import GRAPHQL_TRANSPORT_WS_PROTOCOL

if TYPE_CHECKING:
    from bramble._schema import Schema

Scope = Mapping[str, Any]
Receive = Callable[[], Awaitable[dict[str, Any]]]
Send = Callable[[dict[str, Any]], Awaitable[None]]


class _RawUploadFile:
    """The value a resolver sees for an `Upload!` argument over this adapter -- mirrors the shape
    of Starlette's `UploadFile` (an async `read()`, a `filename`) closely enough for the same
    resolver code to work against either adapter, without actually depending on Starlette.
    """

    def __init__(self, filename: str | None, data: bytes) -> None:
        self.filename = filename
        self._data = data

    async def read(self) -> bytes:
        return self._data


class _RawHTTPRequest:
    def __init__(self, scope: Scope, receive: Receive) -> None:
        self.scope = scope
        self._receive = receive
        self._body: bytes | None = None

    async def body(self) -> bytes:
        if self._body is None:
            chunks: list[bytes] = []
            more_body = True
            while more_body:
                message = await self._receive()
                chunks.append(message.get("body", b""))
                more_body = message.get("more_body", False)
            self._body = b"".join(chunks)
        return self._body


class _RawRequestAdapter:
    """Satisfies `bramble.http.base.BaseRequestProtocol` structurally, straight off the raw ASGI
    `scope` -- the one place this module deals with ASGI's own header/query-string wire format.
    """

    def __init__(self, request: _RawHTTPRequest) -> None:
        self._request = request

    @property
    def method(self) -> str:
        return self._request.scope["method"]

    @property
    def query_params(self) -> Mapping[str, str]:
        query_string: bytes = self._request.scope.get("query_string", b"")
        return dict(parse_qsl(query_string.decode("latin-1")))

    @property
    def headers(self) -> Mapping[str, str]:
        return {
            key.decode("latin-1"): value.decode("latin-1")
            for key, value in self._request.scope.get("headers", [])
        }


class _RawResponse:
    def __init__(self, status_code: int, body: bytes, content_type: str) -> None:
        self.status_code = status_code
        self.body = body
        self.content_type = content_type

    async def send(self, send: Send) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": self.status_code,
                "headers": [(b"content-type", self.content_type.encode("latin-1"))],
            }
        )
        await send({"type": "http.response.body", "body": self.body})


class _RawWebSocket:
    """Satisfies `bramble.subscriptions.graphql_transport_ws.WebSocketProtocol` directly against
    the raw ASGI websocket events -- no Starlette `WebSocket` involved.
    """

    def __init__(self, scope: Scope, receive: Receive, send: Send) -> None:
        self.scope = scope
        self._receive = receive
        self._send = send
        self._connect_received = False

    async def _consume_connect(self) -> None:
        if self._connect_received:
            return
        message = await self._receive()
        if message["type"] != "websocket.connect":
            raise RuntimeError(f"Expected a 'websocket.connect' message, got {message['type']!r}")
        self._connect_received = True

    async def accept(self, subprotocol: str | None = None) -> None:
        await self._consume_connect()
        message: dict[str, Any] = {"type": "websocket.accept"}
        if subprotocol is not None:
            message["subprotocol"] = subprotocol
        await self._send(message)

    async def receive_json(self) -> Any:
        message = await self._receive()
        if message["type"] == "websocket.disconnect":
            raise RuntimeError("WebSocket disconnected")
        text = message.get("text")
        if text is None:
            raise ValueError("Expected a text WebSocket message")
        return json.loads(text)

    async def send_json(self, data: Any) -> None:
        await self._send({"type": "websocket.send", "text": json.dumps(data)})

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        await self._consume_connect()
        message: dict[str, Any] = {"type": "websocket.close", "code": code}
        if reason is not None:
            message["reason"] = reason
        await self._send(message)


class GraphQL(AsyncBaseHTTPView[_RawHTTPRequest, _RawResponse, _RawWebSocket, _RawWebSocket]):
    """A plain ASGI application serving `schema` over HTTP (GET/POST, JSON and multipart bodies,
    batching if `schema.config.batching_config` enables it, the GraphiQL IDE on a bare browser
    `GET`) and WebSocket (the `graphql-transport-ws` subscription protocol) -- functionally
    equivalent to `bramble.adapters.starlette.GraphQL`, but without a Starlette dependency.
    """

    def __init__(self, schema: "Schema", *, multipart_uploads_enabled: bool = True) -> None:
        self.schema = schema
        self.multipart_uploads_enabled = multipart_uploads_enabled

    async def get_body(self, request: _RawHTTPRequest) -> bytes:
        return await request.body()

    async def get_form_data(self, request: _RawHTTPRequest) -> Mapping[str, Any]:
        content_type: bytes = b""
        for key, value in request.scope.get("headers", []):
            if key == b"content-type":
                content_type = value
                break

        fields: dict[str, Any] = {}

        def on_field(field: Any) -> None:
            fields[field.field_name.decode("utf-8")] = field.value.decode("utf-8")

        def on_file(file: Any) -> None:
            file.file_object.seek(0)
            data = file.file_object.read()
            filename = file.file_name.decode("utf-8") if file.file_name else None
            fields[file.field_name.decode("utf-8")] = _RawUploadFile(filename=filename, data=data)

        body = await request.body()
        parse_form({"Content-Type": content_type}, io.BytesIO(body), on_field, on_file)
        return fields

    async def get_context(self, request: _RawHTTPRequest | _RawWebSocket) -> Any:
        return {"request": request}

    def create_response(self, response_data: Any) -> _RawResponse:
        return _RawResponse(200, json.dumps(response_data).encode(), "application/json")

    def create_html_response(self, html: str) -> _RawResponse:
        return _RawResponse(200, html.encode(), "text/html; charset=utf-8")

    def is_websocket_request(self, request: _RawHTTPRequest | _RawWebSocket) -> TypeGuard[_RawWebSocket]:
        return isinstance(request, _RawWebSocket)

    async def pick_websocket_subprotocol(self, request: _RawWebSocket) -> str | None:
        offered = request.scope.get("subprotocols", [])
        return GRAPHQL_TRANSPORT_WS_PROTOCOL if GRAPHQL_TRANSPORT_WS_PROTOCOL in offered else None

    async def create_websocket_response(self, request: _RawWebSocket, subprotocol: str | None) -> _RawWebSocket:
        return request

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "websocket":
            websocket = _RawWebSocket(scope, receive, send)
            await self.run(websocket)
            return

        request = _RawHTTPRequest(scope, receive)
        protocol_request = _RawRequestAdapter(request)
        try:
            response = await self.run(request, protocol_request)
        except HTTPException as error:
            response = _RawResponse(
                error.status_code, json.dumps({"errors": [{"message": error.message}]}).encode(), "application/json"
            )
        await response.send(send)
