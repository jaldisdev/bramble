"""FastAPI is built directly on Starlette (`fastapi.Request`/`fastapi.WebSocket` *are*
`starlette.requests.Request`/`starlette.websockets.WebSocket`, re-exported under FastAPI's own
names), so this adapter doesn't implement its own view -- it wires
`bramble.adapters.starlette.GraphQL` (already usable as a plain ASGI callable) directly into an
`APIRouter`'s routes, the same way `bramble.cli`'s dev server wires it into a Starlette app.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter
from starlette.routing import Route, WebSocketRoute

from bramble.adapters.starlette import GraphQL

if TYPE_CHECKING:
    from bramble._schema import Schema


def GraphQLRouter(schema: "Schema", *, path: str = "/", multipart_uploads_enabled: bool = True) -> APIRouter:
    """An `APIRouter` serving `schema` over HTTP (GET/POST) and WebSocket
    (`graphql-transport-ws`) at `path`, ready to `app.include_router(...)` into a FastAPI app.
    """
    view = GraphQL(schema, multipart_uploads_enabled=multipart_uploads_enabled)
    router = APIRouter()
    router.routes.append(Route(path, view, methods=["GET", "POST"]))
    router.routes.append(WebSocketRoute(path, view))
    return router


__all__ = ["GraphQLRouter"]
