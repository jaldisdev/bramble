# Flask

```bash
pip install "bramble[flask]"
```

Flask is WSGI-based, and **WSGI has no WebSocket support at all** -- this
is a hard transport limitation, not a missing feature here.
`bramble.adapters.flask.graphql_view` only ever serves GraphQL over HTTP
(`GET`/`POST`, JSON and multipart bodies, batching if configured). For an
app that needs both HTTP and WebSocket GraphQL, use
[FastAPI](fastapi.md) or [Django](django.md) (paired with Django Channels)
instead.

```python
from flask import Flask
from bramble.adapters.flask import graphql_view

app = Flask(__name__)
app.register_blueprint(graphql_view(schema, path="/graphql"))
```

`graphql_view(schema, *, path="/graphql", multipart_uploads_enabled=True)`
returns a `Blueprint` registering both `GET` and `POST` at `path`. Flask
2.0+ runs an `async def` view function itself (via `asgiref`, pulled in by
bramble's own `flask` extra), so resolvers can be async even though Flask
itself is WSGI.

## Context

Subclass `GraphQLView` directly (the class `graphql_view` wraps) for a
custom context:

```python
from bramble.adapters.flask import GraphQLView as _GraphQLView
from flask import Blueprint

class GraphQLView(_GraphQLView):
    async def get_context(self, request):
        return {"request": request, "user": await authenticate(request)}

def graphql_view(schema, *, path="/graphql"):
    view = GraphQLView(schema)
    blueprint = Blueprint("graphql", __name__)

    @blueprint.route(path, methods=["GET", "POST"])
    async def handle():
        return await view.dispatch_request()

    return blueprint
```

## `@defer`/`@stream` over WSGI

An `@defer`/`@stream` query still streams a real `multipart/mixed`
response over Flask -- the async incremental-delivery generator is bridged
into a sync WSGI iterable one chunk at a time, rather than buffering the
whole thing before responding. Actually delivering those chunks
incrementally to the client (rather than the WSGI server buffering the
full response before sending anything) requires a WSGI server that itself
streams a generator-based response body -- Flask's own default
single-worker development server does **not** do this; use a
production-grade WSGI server that supports streaming responses for this to
have any visible effect.

## Testing

Use Flask's own `test_client()`:

```python
client = app.test_client()
response = client.post("/graphql", json={"query": "{ hello }"})
```
