# Django

```bash
pip install "bramble-graphql[django]"
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

`graphql_view(schema, *, multipart_uploads_enabled=True, graphql_ide=True,
json_encoder=DjangoJSONEncoder, view_class=AsyncGraphQLView)` returns an
`async def` view callable, ready for `urlpatterns` -- Django detects and
runs it via its own async dispatch, no extra wiring needed.

* `multipart_uploads_enabled=False` rejects `multipart/form-data` file
  uploads with a 400.
* `graphql_ide=False` stops a browser `GET` with no `query` from serving
  the GraphiQL page -- what a production endpoint wants. Plain `GET`
  queries keep working.
* `json_encoder` is the `json.JSONEncoder` subclass the response body is
  serialised with; it defaults to Django's own.

### Subclassing the view

For anything beyond those arguments -- your own context object, your own
error handling -- mount `AsyncGraphQLView` (or a subclass) directly:

```python
# views.py
from bramble.adapters.django.views import AsyncGraphQLView

class MyGraphQLView(AsyncGraphQLView):
    async def get_context(self, request):
        return MyContext(request=request, response=self.sub_response, user=request.user)

# urls.py
urlpatterns = [
    path("graphql", csrf_exempt(MyGraphQLView.as_view(schema=schema))),
]
```

`as_view(**initkwargs)` takes the same arguments the class's constructor
does and returns the async view callable, mirroring Django's own
`View.as_view()` -- including that decorators wrap what it returns. It is
deliberately *not* a Django `View` subclass: the GraphQL request cycle is
one POST handler rather than a method-dispatch table.

A fresh view instance is built per request, so anything a hook stores on
`self` is request-scoped. The overridable hooks are `get_context`,
`get_root_value`, `create_response`, `create_html_response`,
`create_multipart_response`, and `dispatch`.

### Setting the response status code

`self.sub_response` is a `bramble.http.types.TemporalResponse` -- a
`status_code`/`headers` stand-in available *during* execution, since the
real response only exists once the body has been built. Put it in your
context and a resolver (or a `SchemaExtension`) can set the status code
while a field is still resolving:

```python
@bramble.field
def me(info: bramble.Info) -> User | None:
    if not info.context.user.is_authenticated:
        info.context.response.status_code = 401
        return None
    return load_user(info.context.user)
```

A status code other than `200`, and any headers set this way, are copied
onto the real response after execution. A request that fails before
execution (a 400 or 405) is unaffected -- there was no resolver to
override it.

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

Over HTTP, the default context is `{"request": request, "response":
self.sub_response}` -- the real `django.http.HttpRequest`, reachable in a
resolver via `info.context["request"]`, plus the response stand-in
described above. Since Django's own middleware (including auth) already
runs before the view executes, whatever it attaches to `request`
(`request.user`, a session, ...) is already available there; override
`get_context` when you want a typed object rather than a dict.

Over WebSocket, the default context is `{"scope": self.scope,
"connection_params": ..., "ws": self}` -- Channels' own connection scope
(where its middleware leaves the session, user, headers, and URL route),
the payload the client sent with `connection_init`, and the consumer
itself. Override `GraphQLWSConsumer.get_context(connection_params)` to
build your own:

```python
class MyGraphQLWSConsumer(GraphQLWSConsumer):
    async def get_context(self, connection_params):
        return MyContext(user=self.scope["user"], ws=self, params=connection_params)
```

It is called once per operation on the socket, and the consumer instance
lives as long as the connection does.

## Channel-layer subscriptions

A subscription usually needs to receive events produced elsewhere in the
application -- a signal handler, a Celery task, another request. Channels'
answer is the channel layer, and `listen_to_channel` is how a resolver
consumes one:

```python
@bramble.type
class Subscription:
    @bramble.subscription
    async def cart(info: bramble.Info, cart_id: str) -> AsyncGenerator[Cart, None]:
        websocket = info.context["ws"]
        async with websocket.listen_to_channel("live.cart", groups=[f"cart.{cart_id}"]) as messages:
            async for message in messages:
                yield Cart(**message["payload"])
```

Anywhere else in the application, produce events with a plain
`group_send`:

```python
from channels.layers import get_channel_layer

await get_channel_layer().group_send("cart.42", {"type": "live.cart", "payload": {...}})
```

Messages are matched on `type` alone and the whole message dict is
yielded, so any keys the producer set come through untouched. The groups
are joined on entry and discarded on exit -- including when the client
unsubscribes or the socket drops, since that cancels the resolver's task
and unwinds the context manager with it.

`self.channel_layer` and `self.channel_name` are Channels' own attributes,
available on the consumer for anything `listen_to_channel` doesn't cover
(joining a group by hand, sending to one). A configured
`settings.CHANNEL_LAYERS` is required; without one, `listen_to_channel`
raises rather than yielding a generator that could never produce anything.

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
