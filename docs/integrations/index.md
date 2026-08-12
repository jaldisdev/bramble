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
