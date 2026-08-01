# Raw ASGI

```bash
pip install "bramble[asgi]"
```

`bramble.adapters.asgi.GraphQL` implements the exact same behavior as the
[Starlette integration](starlette.md), but with **no Starlette dependency
at all** -- built directly against the bare ASGI spec. Pick this one to
keep bramble's own footprint as the only new dependency in an app that
doesn't already depend on Starlette for anything else.

It's a plain ASGI application, usable directly or mounted under any ASGI
router:

```python
from bramble.adapters.asgi import GraphQL

app = GraphQL(schema)
```

```python
import uvicorn
uvicorn.run(app, host="0.0.0.0", port=8000)
```

Serves the same request shape as every other integration: `GET`/`POST`
HTTP at whatever path it's mounted under, plus WebSocket
`graphql-transport-ws` subscriptions on that same path -- see
[Starlette](starlette.md) for the full request-shape reference.
`GraphQL(schema, *, multipart_uploads_enabled=True)`.

## Context

Override `get_context` on a subclass, the same way as every other
integration:

```python
from bramble.adapters.asgi import GraphQL as _GraphQL

class GraphQL(_GraphQL):
    async def get_context(self, request):
        return {"request": request, "user": await authenticate(request)}
```

## Testing

`GraphQL(schema)` is a real ASGI app, testable with any ASGI-aware HTTP
client, e.g. `httpx`'s `ASGITransport`:

```python
import httpx

transport = httpx.ASGITransport(app=GraphQL(schema))
async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
    response = await client.post("/graphql", json={"query": "{ hello }"})
```
