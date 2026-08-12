# Integrations

bramble ships an HTTP (and, where the framework supports it, WebSocket)
integration for five frameworks, all built on the same framework-agnostic
core (`bramble.http.async_base_view.AsyncBaseHTTPView`) -- request
parsing, batching, file upload, and `@defer`/`@stream` multipart streaming
all behave identically across every one of them.

| Framework | Extra | HTTP | WebSocket subscriptions |
| --- | --- | --- | --- |
| [Starlette](starlette.md) | `bramble[starlette]` | Yes | Yes |
| [Raw ASGI](asgi.md) | `bramble[asgi]` | Yes | Yes |
| [FastAPI](fastapi.md) | `bramble[fastapi]` | Yes | Yes |
| [Flask](flask.md) | `bramble[flask]` | Yes | No (WSGI has no WebSocket support) |
| [Django](django.md) | `bramble[django]` | Yes | Yes, via Django Channels |

Each extra installs bramble alongside that framework's own dependencies,
e.g.:

```bash
pip install "bramble[fastapi]"
```

Every integration accepts a `multipart_uploads_enabled: bool = True`
constructor option (see [File upload](../guides/file-upload.md)), serves a
GraphiQL IDE on a plain browser `GET` request with no `query` parameter
unless you pass `graphql_ide=False`, and supports
[query batching](../guides/query-batching.md) when
`SchemaConfig.batching_config` is set.

## Which one to pick

- Already using **FastAPI** or **Django**? Use that integration directly.
- Building a new ASGI app with no framework preference? **Starlette** is
  the least code to wire up, including WebSocket.
- Want the smallest dependency footprint (no Starlette at all)? **Raw
  ASGI** implements the same behavior directly against the bare ASGI
  spec.
- Only need HTTP, no subscriptions, and specifically want WSGI (e.g. an
  existing Flask app)? **Flask** -- see its own page for the tradeoffs of
  streaming `@defer`/`@stream` responses over WSGI.

## `bramble dev`

For local development against any of these, `bramble dev` (see
[Tools](../guides/tools.md)) spins up a Starlette-based server with
GraphiQL and auto-reload with zero setup -- useful even if the target
production integration is a different framework, since the underlying
execution behavior is identical either way.

## Writing your own integration

The five above are not a closed set -- the pieces an adapter is built from are
public API, and `bramble.http` exports them:

```python
from bramble.http import AsyncBaseHTTPView, BaseRequestProtocol, BaseView
```

| Export | Role |
| --- | --- |
| `AsyncBaseHTTPView` | the async HTTP view to subclass; owns request parsing, execution and result encoding |
| `BaseView` | the shared, framework-agnostic half (GraphiQL, content negotiation, encoding) |
| `BaseRequestProtocol` | the structural contract your framework's request object must satisfy |

An adapter's job is to translate between its framework's request/response
objects and these, and to implement the handful of abstract methods
`AsyncBaseHTTPView` declares -- reading the body, the query string, and the
form data. The shipped adapters under `bramble/adapters/` are the worked
examples; the Django one is the most complete, since it also covers file
uploads and WebSocket.

Subscriptions have the matching hook: `bramble.subscriptions` exports
`GraphQLTransportWSHandler`, the transport-agnostic implementation of the
`graphql-transport-ws` protocol. See [Subscriptions](../general/subscriptions.md).

Because these are exported rather than reached by deep import, they are covered
by the same compatibility expectations as the rest of the public API -- an
adapter you maintain outside this repository will not break on a patch release.
