# Django

```bash
pip install "bramble[django]"
```

Django's own views (sync or async) can't hold a long-lived duplex socket,
so HTTP and WebSocket are two separate pieces here: an async Django view
for HTTP, and a [Django Channels](https://channels.readthedocs.io/)
consumer for WebSocket subscriptions -- bramble's `django` extra pulls in
`channels` directly, since both are expected out of the box. Requires
Django 4.2+ (needed for `StreamingHttpResponse`'s async-iterator support,
used to serve `@defer`/`@stream` responses).

## HTTP

```python
# urls.py
from django.urls import path
from bramble.adapters.django.views import graphql_view
from myapp.schema import schema

urlpatterns = [
    path("graphql", graphql_view(schema)),
]
```

`graphql_view(schema, *, multipart_uploads_enabled=True)` returns an
`async def` view callable, ready for `urlpatterns` -- Django detects and
runs it via its own async dispatch, no extra wiring needed.

## WebSocket subscriptions (Channels)

```python
# asgi.py
from channels.routing import ProtocolTypeRouter, URLRouter
from django.urls import re_path
from bramble.adapters.django.channels import GraphQLWSConsumer
from myapp.schema import schema

application = ProtocolTypeRouter({
    "websocket": URLRouter([
        re_path(r"^graphql$", GraphQLWSConsumer.as_asgi(schema=schema)),
    ]),
})
```

## Context

The default context passed to every resolver is `{"request": request}` --
the real `django.http.HttpRequest`, reachable in a resolver via
`info.context["request"]`. Since Django's own middleware (including auth)
already runs before the view executes, whatever it attaches to `request`
(`request.user`, a session, ...) is already available there -- no separate
context-building hook is needed for the common case the way the ASGI-based
integrations expose `get_context` for.

## Testing

Django's own `RequestFactory` builds requests without needing a full
running server:

```python
from django.test import RequestFactory

view = graphql_view(schema)
request = RequestFactory().post("/graphql", data={"query": "{ hello }"}, content_type="application/json")
response = await view(request)
```

For the WebSocket consumer, use Channels' own `WebsocketCommunicator`:

```python
from channels.testing import WebsocketCommunicator

app = GraphQLWSConsumer.as_asgi(schema=schema)
communicator = WebsocketCommunicator(app, "/graphql", subprotocols=["graphql-transport-ws"])
connected, subprotocol = await communicator.connect()
```
