# Authentication

bramble has no built-in authentication/permission system -- access control
is ordinary Python, using whatever a request-scoped `context` object
carries. Every HTTP integration's `get_context` hook is the place to build
that context per request; the default implementation on every adapter
hands the raw framework request itself through as `{"request": request}`,
and can be overridden (by subclassing the view) to build something richer:

```python
from bramble.adapters.starlette import GraphQL as _GraphQL

class GraphQL(_GraphQL):
    async def get_context(self, request):
        token = request.headers.get("authorization")
        user = await authenticate(token) if token else None
        return {"request": request, "user": user}
```

A resolver reads `info.context["user"]` (or, with a dataclass/typed
context object instead of a raw dict, `info.context.user`) and raises
`bramble.GraphQLError` for an unauthenticated/unauthorized request, exactly
like any other resolver-level error:

```python
@bramble.type
class Query:
    @bramble.field
    def me(info: bramble.Info) -> "User":
        user = info.context["user"]
        if user is None:
            raise bramble.GraphQLError("not authenticated", code=bramble.ErrorCode.ARGUMENT_TYPE_MISMATCH)
        return user
```

For a WebSocket subscription, the same idea applies via
`GraphQLTransportWSHandler.build_context` (override it on a subclass) --
its default context is the `connection_init` message's own payload
alongside the socket itself, so a client can pass an auth token at
connection time:

```python
from bramble.subscriptions.graphql_transport_ws import GraphQLTransportWSHandler

class AuthenticatedHandler(GraphQLTransportWSHandler):
    async def build_context(self):
        token = (self.connection_params or {}).get("authToken")
        user = await authenticate(token) if token else None
        return {"user": user, "websocket": self.websocket}
```

Then point the adapter at it: e.g.
`GraphQL(schema)` on `bramble.adapters.starlette`/`asgi` has a
`graphql_transport_ws_handler_class` class attribute that can be overridden
the same way.

## Declaring intent with a schema directive

[Schema directives](../types/schema-directives.md) are purely declarative
and carry no runtime behavior of their own -- `@auth(role: "admin")` on a
field documents that a check applies, but doesn't enforce it. Combine one
with the context-based approach above: the directive documents the
requirement in SDL/introspection for tooling and other developers, while
the resolver itself (reading `info.context`) is what actually enforces it.
