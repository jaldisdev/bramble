# File upload

`bramble.Upload` is a scalar for file-upload arguments. It's entirely
opaque: `serialize`/`parse_value` are both identity functions, so whatever
object a request's transport layer puts into `variable_values` (raw bytes,
a file-like object with an `async def read()`, ...) passes straight through
to the resolver unchanged. bramble has no HTTP transport layer of its own
-- every [integration](../integrations/index.md) parses the
[GraphQL multipart request spec](https://github.com/jaydenseric/graphql-multipart-request-spec)'s
`operations`/`map` fields (via `multipart/form-data`) and hands the result
to `Schema.execute_async` the same way any other argument would be.

```python
import bramble

@bramble.type
class Query:
    @bramble.field
    async def upload_size(file: bramble.Upload) -> int:
        content = await file.read()  # type: ignore[attr-defined]
        return len(content)

schema = bramble.Schema(query=Query)
```

A client sends this as a `multipart/form-data` POST with an `operations`
field (the GraphQL request, `null` where the file belongs), a `map` field
(mapping each file part's form field name to the dotted path it replaces in
`operations`), and the file itself as its own form part:

```
operations: {"query": "query($f: Upload!) { uploadSize(file: $f) }", "variables": {"f": null}}
map:        {"0": ["variables.f"]}
0:          <the file's bytes>
```

Every HTTP integration ([Starlette](../integrations/starlette.md),
[raw ASGI](../integrations/asgi.md), [FastAPI](../integrations/fastapi.md),
[Flask](../integrations/flask.md), [Django](../integrations/django.md))
supports this out of the box, controlled by each view's own
`multipart_uploads_enabled` flag (`True` by default):

```python
from bramble.adapters.starlette import GraphQL

app = GraphQL(schema, multipart_uploads_enabled=False)  # reject multipart requests entirely
```

The concrete Python object a resolver receives for `Upload` differs by
integration -- Starlette/FastAPI hand over a real `starlette.datastructures.UploadFile`;
raw ASGI, Flask, and Django each wrap their own framework's upload object
in a small adapter. All of them expose the same four members, so a resolver
written against these works portably across every integration:

| Member | |
| --- | --- |
| `await read()` | the file's bytes |
| `filename` | as the client sent it, or `None` |
| `content_type` | the MIME type the client declared, or `None` |
| `size` | in bytes, or `None` where the framework does not report it |

`content_type` in particular is worth reaching for rather than sniffing the
filename extension -- picking a storage bucket, recording a `mimetype` column,
or rejecting a disallowed kind are all better keyed off what the client
declared:

```python
@bramble.field
async def upload_avatar(file: bramble.Upload) -> str:
    if file.content_type not in {"image/png", "image/jpeg"}:
        raise bramble.GraphQLError(
            "avatars must be PNG or JPEG", code=bramble.ErrorCode.FIELD_RESOLUTION_FAILED
        )
    ...
```

Treat both metadata attributes as client-supplied: they are declared by the
uploading client, not verified by bramble.

## Registering `Upload` for SDL

`Upload` (like any other scalar reference) round-trips correctly through
execution without registration. Register it via `SchemaConfig(scalar_map={bramble.Upload: bramble.UploadDefinition})`
if you also want `scalar Upload` (with its description) to actually appear
in `Schema.to_sdl()`'s output:

```python
from bramble.schema.config import SchemaConfig

schema = bramble.Schema(
    query=Query,
    config=SchemaConfig(scalar_map={bramble.Upload: bramble.UploadDefinition}),
)
```
