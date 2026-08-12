# Bramble GraphQL

A GraphQL library for Python, with parsing, validation, and query lowering
implemented in Rust and exposed through a thin PyO3 extension. The
schema-declaration API (decorators, type resolution, directives) is pure
Python and dataclass-based.

## Features

- Decorator-based schema definition: `@bramble.type`, `@bramble.interface`,
  `@bramble.input`, `@bramble.union`, `@bramble.scalar`
- Real dataclasses under the hood — `@bramble.type`-decorated classes are
  ordinary dataclasses, not a parallel object model
- Custom scalars, schema directives, and operation directives (`@skip`,
  `@include`, and user-defined directives), each with location validation
- Async and sync execution (`Schema.execute` / `Schema.execute_async`), with
  spec-correct null bubbling, fragment/field merging, and concurrent field
  and list-item resolution (mutations execute their root fields serially,
  per spec)
- SDL rendering (`Schema.to_sdl()`) and Automatic Persisted Queries
- Rust-based parsing and validation for performance and spec conformance

## Installation

Requires Python 3.10+ and a Rust toolchain (for building from source; no
prebuilt wheels are published yet).

```bash
pip install -e ".[dev]"
```

This uses [maturin](https://www.maturin.rs/) to build the Rust extension
in place.

## Quickstart

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

See [`examples/blog`](examples/blog/schema.py) for a fuller schema covering
interfaces, unions, custom scalars, schema/operation directives, mutations,
and async resolvers.

## Development

```bash
# Rust
cargo test --workspace
cargo clippy --workspace --all-targets

# Python (rebuild the extension after any Rust change)
maturin develop
pytest
```

## Project layout

- `crates/bramble-core` — pure Rust: parsing, validation, lowering, SDL
  rendering, error types. No Python dependency.
- `crates/bramble-py` — PyO3 bindings exposing `bramble-core` to Python.
- `bramble/` — the Python package: schema-declaration decorators, execution
  engine, and the public API.
- `tests/` — Python test suite.
- `examples/` — example schemas.

## License

Dual-licensed under [MIT](LICENSE-MIT) or [Apache 2.0](LICENSE-APACHE), at
your option.
