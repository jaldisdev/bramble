# Starlette

```bash
pip install "bramble[starlette]"
```

`bramble.adapters.starlette.GraphQL` is a plain ASGI application -- mount
it directly, or wire it into routes for both HTTP and WebSocket at the
same path:

```python
from starlette.applications import Starlette
from starlette.routing import Route, WebSocketRoute
from bramble.adapters.starlette import GraphQL

graphql_app = GraphQL(schema)

app = Starlette(routes=[
    Route("/graphql", graphql_app, methods=["GET", "POST"]),
    WebSocketRoute("/graphql", graphql_app),
])
```

`GraphQL(schema, *, multipart_uploads_enabled=True, graphql_ide=True)` serves:

- `GET /graphql` -- executes a query from `?query=...` (and `?variables=`/
  `?operationName=`), or serves the GraphiQL IDE if `query` is absent and
  the request accepts HTML.
- `POST /graphql` -- executes a query from a JSON body, a batch (if
  configured), a `multipart/form-data` file upload request, or (for a query
  using `@defer`/`@stream`) streams a `multipart/mixed` response.
- WebSocket `/graphql` -- `graphql-transport-ws` subscriptions.

## Context

Override `get_context` on a subclass to build a request-scoped context
richer than the default `{"request": request}`:

```python
from bramble.adapters.starlette import GraphQL as _GraphQL

class GraphQL(_GraphQL):
    async def get_context(self, request):
        return {"request": request, "user": await authenticate(request)}
```

See [Authentication](../guides/authentication.md).

## Testing

`GraphQL(schema)` is a real ASGI app, usable directly with `httpx`'s
`ASGITransport` or Starlette's own `TestClient`:

```python
import httpx

transport = httpx.ASGITransport(app=GraphQL(schema))
async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
    response = await client.post("/graphql", json={"query": "{ hello }"})
```
