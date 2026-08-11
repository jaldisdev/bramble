# Bramble

Bramble is a GraphQL library for Python. Schemas are declared with plain
dataclass-based decorators (`@bramble.type`, `@bramble.interface`,
`@bramble.input`, `@bramble.union`, `@bramble.scalar`), and executed by a
Rust-based core (parsing, validation, and query lowering implemented in Rust
via a thin PyO3 extension) for speed and spec conformance.

```python
import bramble

@bramble.type
class Query:
    @bramble.field
    def hello(name: str = "world") -> str:
        return f"Hello, {name}!"

schema = bramble.Schema(query=Query)

result = schema.execute("{ hello }")
# {'data': {'hello': 'Hello, world!'}}

print(schema.to_sdl())
# type Query {
#   hello(name: String!): String!
# }
```

## Installation

Bramble requires Python 3.10+. Building from source also requires a Rust
toolchain (no prebuilt wheels are published yet):

```bash
pip install -e ".[dev]"
```

This uses [maturin](https://www.maturin.rs/) to build the Rust extension in
place. After any change to the Rust crates, rebuild with `maturin develop`
before Python picks up the change.

To pull in a specific HTTP framework's dependencies alongside bramble
itself, install one of the extras documented in
[Integrations](integrations/index.md), e.g. `pip install "bramble[fastapi]"`.

## Where to go next

- **[General](general/schema-basics.md)** -- the basics: building a schema,
  and writing queries, mutations, and subscriptions.
- **[Types](types/object-types.md)** -- the full type-declaration API: object
  types, resolvers, input types, interfaces, unions, [enums](types/enums.md),
  scalars, directives, [dependency injection](types/dependency-injection.md),
  and more.
- **[Guides](guides/file-upload.md)** -- task-oriented guides: file uploads,
  [introspection](guides/introspection.md), persisted queries, query
  batching, testing, and the `bramble` CLI.
- **[Federation](federation/introduction.md)** -- building an Apollo
  Federation v2 subgraph.
- **[Integrations](integrations/index.md)** -- serving a schema over HTTP
  (and WebSocket) with Starlette, raw ASGI, FastAPI, Flask, or Django.
- **[Codegen](codegen/query-codegen.md)** -- generating typed client code
  from `.graphql` query files.

The [`examples/`](../examples) directory has two complete, runnable schemas
this documentation draws its examples from: `examples/blog` (interfaces,
unions, custom scalars, directives, mutations) and
`examples/federation_products` (a minimal Federation v2 subgraph).
