# FastAPI

```bash
pip install "bramble[fastapi]"
```

`bramble.adapters.fastapi.GraphQLRouter` builds an `APIRouter` serving
`schema` over HTTP and WebSocket at a given path -- FastAPI's own
`Request`/`WebSocket` are Starlette's under the hood, so this integration
is a thin wrapper reusing [`bramble.adapters.starlette.GraphQL`](starlette.md)
directly rather than reimplementing anything:

```python
from fastapi import FastAPI
from bramble.adapters.fastapi import GraphQLRouter

app = FastAPI()
app.include_router(GraphQLRouter(schema, path="/graphql"))
```

`GraphQLRouter(schema, *, path="/", multipart_uploads_enabled=True)`
registers both the HTTP route (`GET`/`POST`) and the WebSocket route at
`path`.

## Context

`GraphQLRouter` doesn't expose a `get_context` override directly (it's a
function returning a router, not a subclassable view) -- for a custom
context, build the underlying `bramble.adapters.starlette.GraphQL` view
yourself and wire it into FastAPI's routing directly, the same way
`GraphQLRouter` does internally:

```python
from fastapi import APIRouter
from starlette.routing import Route, WebSocketRoute
from bramble.adapters.starlette import GraphQL as _GraphQL

class GraphQL(_GraphQL):
    async def get_context(self, request):
        return {"request": request, "user": await authenticate(request)}

router = APIRouter()
view = GraphQL(schema)
router.routes.append(Route("/graphql", view, methods=["GET", "POST"]))
router.routes.append(WebSocketRoute("/graphql", view))

app.include_router(router)
```

## Testing

Use FastAPI's own `TestClient` (backed by `httpx`) against the app directly:

```python
from fastapi.testclient import TestClient

client = TestClient(app)
response = client.post("/graphql", json={"query": "{ hello }"})
```
