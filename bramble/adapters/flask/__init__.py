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

"""Flask is WSGI-based, and **WSGI has no WebSocket support at all** -- this is a hard transport
limitation, not a missing feature here. `GraphQLView` below only ever serves GraphQL-over-HTTP
(GET/POST, JSON and multipart bodies, batching if configured). For a single app that needs both
HTTP and WebSocket GraphQL, use `bramble.adapters.fastapi` or `bramble.adapters.django` (paired
with Django Channels) instead.

Flask 2.0+ runs an `async def` view function itself (via `asgiref`, bramble's own `flask` extra
pulls it in) -- so `GraphQLView`'s methods are plain `async def` and Flask handles both sync (WSGI)
and async serving without a separate code path here.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterator, Mapping
from typing import TYPE_CHECKING, Any

from flask import Blueprint, Response
from flask import request as flask_request

from bramble.http.async_base_view import AsyncBaseHTTPView
from bramble.http.exceptions import HTTPException
from bramble.http.multipart import multipart_content_type

if TYPE_CHECKING:
    from werkzeug.datastructures import FileStorage
    from werkzeug.wrappers import Request as WerkzeugRequest

    from bramble._schema import Schema


class _FlaskRequestAdapter:
    """Satisfies `bramble.http.base.BaseRequestProtocol` structurally, over Flask's own
    (thread-local/contextvar) `request` proxy.
    """

    def __init__(self, request: "WerkzeugRequest") -> None:
        self._request = request

    @property
    def method(self) -> str:
        return self._request.method

    @property
    def query_params(self) -> Mapping[str, str]:
        return self._request.args

    @property
    def headers(self) -> Mapping[str, str]:
        return self._request.headers


class _FlaskUploadFile:
    """The value a resolver sees for an `Upload!` argument -- wraps Werkzeug's `FileStorage`
    (whose own `read()` is synchronous) with an `async def read()` so resolver code stays
    portable across adapters.
    """

    def __init__(self, storage: "FileStorage") -> None:
        self.filename = storage.filename
        self._storage = storage

    async def read(self) -> bytes:
        return self._storage.read()


def _sync_iterator_from_async(async_iterator: AsyncIterator[bytes]) -> Iterator[bytes]:
    """Bridges an async byte iterator into a sync one a WSGI server can pull chunk-by-chunk.

    Flask's async view support (via `asgiref`) only keeps an event loop alive for the view
    function's own execution -- that call has already returned the `Response` object by the time
    WSGI actually iterates its body, so pulling further chunks needs its own event loop, run one
    step at a time as the WSGI server calls `next()`. This is what makes real (non-buffered)
    streaming possible over WSGI at all; it still needs a WSGI server that itself streams a
    generator-based response body rather than fully draining it upfront (the default single sync
    worker in Flask's own dev server does NOT do this) to actually deliver chunks incrementally to
    the client.
    """
    loop = asyncio.new_event_loop()
    try:
        while True:
            try:
                yield loop.run_until_complete(async_iterator.__anext__())
            except StopAsyncIteration:
                return
    finally:
        loop.close()


class GraphQLView(AsyncBaseHTTPView[Any, Response, Any, Any]):
    """Serves `schema` over HTTP only -- see this module's own docstring for why there's no
    WebSocket support here.
    """

    def __init__(
        self, schema: "Schema", *, multipart_uploads_enabled: bool = True, graphql_ide: bool = True
    ) -> None:
        self.schema = schema
        self.multipart_uploads_enabled = multipart_uploads_enabled
        self.graphql_ide = graphql_ide

    async def get_body(self, request: "WerkzeugRequest") -> bytes:
        return request.get_data()

    async def get_form_data(self, request: "WerkzeugRequest") -> Mapping[str, Any]:
        fields: dict[str, Any] = dict(request.form)
        for name, storage in request.files.items():
            fields[name] = _FlaskUploadFile(storage)
        return fields

    async def get_context(self, request: "WerkzeugRequest") -> Any:
        return {"request": request}

    def create_response(self, response_data: Any) -> Response:
        return Response(json.dumps(response_data), mimetype="application/json")

    def create_html_response(self, html: str) -> Response:
        return Response(html, mimetype="text/html")

    async def create_multipart_response(self, stream: AsyncIterator[bytes]) -> Response:
        return Response(
            _sync_iterator_from_async(stream),
            content_type=multipart_content_type(),
            direct_passthrough=True,
        )

    def is_websocket_request(self, request: Any) -> Any:
        return False

    async def dispatch_request(self) -> Response:
        protocol_request = _FlaskRequestAdapter(flask_request)
        try:
            return await self.run(flask_request, protocol_request)
        except HTTPException as error:
            return Response(
                json.dumps({"errors": [{"message": error.message}]}),
                status=error.status_code,
                mimetype="application/json",
            )


def graphql_view(
    schema: "Schema", *, path: str = "/graphql", multipart_uploads_enabled: bool = True, graphql_ide: bool = True
) -> Blueprint:
    """A Flask `Blueprint` serving `schema` at `path`, ready for `app.register_blueprint(...)`."""
    view = GraphQLView(schema, multipart_uploads_enabled=multipart_uploads_enabled, graphql_ide=graphql_ide)
    blueprint = Blueprint("bramble_graphql", __name__)

    @blueprint.route(path, methods=["GET", "POST"])
    async def handle() -> Response:
        return await view.dispatch_request()

    return blueprint


__all__ = ["GraphQLView", "graphql_view"]
