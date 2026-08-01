# The `bramble` CLI

Installing the `cli` extra (`pip install "bramble[cli]"`) provides a
`bramble` command with two subcommands: `dev` (a local development server
with GraphiQL) and `codegen` (typed client code generation from `.graphql`
query files).

Both subcommands take a `--app-dir` option (default `.`) and a schema
selector -- a `module.path:symbol` string. If the resolved symbol is
callable rather than a `Schema` instance directly, it's called with no
arguments to build one (useful for a `build_schema()` factory function, as
in `examples/blog/schema.py`):

```bash
bramble dev myapp.schema:schema
# or, with a factory function:
bramble dev myapp.schema:build_schema
```

## `bramble dev`

```bash
bramble dev myapp.schema:schema [-h HOST] [-p PORT] [--log-level LEVEL] [--app-dir DIR]
```

Starts a local ASGI development server (via `uvicorn`, with auto-reload
watching `--app-dir`) serving `schema` at `http://<host>:<port>/graphql` --
both `/` and `/graphql` serve the schema, over HTTP and WebSocket, with
GraphiQL available in a browser. `bramble dev` fails fast with a clear
error if the schema selector can't be resolved, before uvicorn even starts.

## `bramble codegen`

```bash
bramble codegen QUERY [QUERY ...] --schema myapp.schema:schema -o OUTPUT_DIR -p PLUGIN [-p PLUGIN ...]
```

Generates typed client code from one or more `.graphql` query files. See
[Query codegen](../codegen/query-codegen.md) for the full reference.

```bash
bramble codegen queries/get_post.graphql --schema myapp.schema:schema -o generated/ -p python
```
