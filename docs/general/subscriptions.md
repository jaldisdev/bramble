# Subscriptions

A `subscription` root field's resolver is an **async generator**: each
value it yields becomes one independent, self-contained response delivered
to the client, for as long as the subscription stays open.

```python
import bramble
from collections.abc import AsyncGenerator

@bramble.type
class Subscription:
    @bramble.field
    async def count(upto: int) -> AsyncGenerator[int, None]:
        for i in range(upto):
            yield i

schema = bramble.Schema(query=Query, subscription=Subscription)
```

Run a subscription with `Schema.subscribe_async` (there is no synchronous
variant -- a subscription is an open-ended event stream, not a single value
a blocking call could return):

```python
import asyncio

async def main() -> None:
    async for response in schema.subscribe_async("subscription { count(upto: 3) }"):
        print(response)
    # {'data': {'count': 0}}
    # {'data': {'count': 1}}
    # {'data': {'count': 2}}

asyncio.run(main())
```

An error raised while producing one event (either by the generator itself,
or while resolving that event's own selection set) is scoped to that single
response's `"errors"` list -- it doesn't end the subscription; the generator
keeps running and later events still deliver normally.

## Serving subscriptions over WebSocket

Over HTTP, subscriptions are served via the `graphql-transport-ws`
WebSocket subprotocol -- the current standard subscription transport, and
what a modern GraphQL IDE speaks by default. Every bramble HTTP integration
that supports WebSocket (Starlette, raw ASGI, FastAPI, and Django+Channels;
Flask is HTTP-only) wires this in automatically. See
[Integrations](../integrations/index.md) for per-framework setup, and
`bramble.subscriptions.GraphQLTransportWSHandler` if you're building a
custom transport against a WebSocket-like object of your own -- it needs
only `accept`/`receive_json`/`send_json`/`close`.

The legacy `graphql-ws` subprotocol is not supported; a client offering only
that subprotocol has its WebSocket connection closed with code `4406`.

### Protocol close codes

Protocol violations close the socket with the code `graphql-transport-ws`
defines for them, so a client can tell them apart from an ordinary
disconnect:

| Code | When |
| --- | --- |
| `4400` | A malformed message: no `type`, a `subscribe`/`complete` without a string `id`, a `subscribe` without a payload, or an unrecognised message type. |
| `4401` | An operation arrived before `connection_init`. |
| `4406` | The client offered no subprotocol bramble speaks. |
| `4409` | A `subscribe` reused an `id` that already has a running operation. |
| `4429` | A second `connection_init` on the same socket. |

`ping` is answered with `pong` (echoing the ping's `payload` if it had one),
including before `connection_init` -- it's a liveness check, not an
operation. An unsolicited `pong` from the client is accepted and needs no
reply.

### Cleanup on disconnect

When a client unsubscribes, sends `complete`, or drops the connection, the
subscription's own generator is closed **before** bramble moves on -- so a
`finally` block in your resolver (unsubscribing from a broker, closing a
cursor, releasing a channel) runs promptly and deterministically, not
whenever the garbage collector eventually gets to it:

```python
@bramble.field
async def messages() -> AsyncGenerator[Message, None]:
    channel = await broker.subscribe("messages")
    try:
        async for message in channel:
            yield message
    finally:
        await channel.close()   # runs at unsubscribe, not at GC
```

The same guarantee covers generator-based
[dependency providers](../types/dependency-injection.md) used by a
subscription.
