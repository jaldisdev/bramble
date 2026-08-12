# Schema

`bramble.Schema` compiles a set of `@bramble.type`-decorated root types into
one validated, executable schema.

```python
bramble.Schema(
    query: type,
    mutation: type | None = None,
    subscription: type | None = None,
    directives: Sequence[Callable] = (),
    types: Sequence[type] = (),
    config: SchemaConfig | None = None,
    execution_context_class: type | None = None,
    schema_directives: Sequence[object] = (),
)
```

- **`query`** -- required; must be an `@bramble.type`-decorated class.
- **`mutation`**, **`subscription`** -- optional root types; see
  [Mutations](../general/mutations.md) and
  [Subscriptions](../general/subscriptions.md).
- **`directives`** -- custom [operation directive](operation-directives.md)
  functions (`@bramble.directive(...)`-decorated) usable in a query
  document, e.g. `@shout`.
- **`types`** -- extra types to include even if not reachable from any
  field's own return type or resolver argument (a common case: an
  interface's implementor that's never directly returned from a
  field typed as the interface itself).
- **`config`** -- a [`SchemaConfig`](schema-configurations.md) controlling
  naming, custom scalar registration, and batching.
- **`execution_context_class`** -- if given, instantiated with no arguments
  and used as the resolver-facing `context` for any call to
  `execute`/`execute_async`/`subscribe_async`/`execute_incremental` that
  doesn't pass its own `context=...` explicitly. Useful when a caller (an
  HTTP integration, a test) can reasonably be expected to always supply its
  own context, but you still want a sensible default for ad hoc calls.
- **`schema_directives`** -- applied [schema directive](schema-directives.md)
  instances attached to the `schema { ... }` block itself (there's no
  `@bramble.type`-decorated class representing the schema itself to attach
  these to any other way) -- this is how
  [`bramble.federation.Schema`](../federation/introduction.md) applies its
  own `@link` directive, for instance.

Building a `Schema()` walks every type reachable from the roots, validates
interface implementations (a subclass can't weaken a non-null interface
field to nullable, or add a new required argument the interface doesn't
declare), and compiles the result once -- every subsequent
parse/validate/execute cycle runs against this compiled schema, not against
the decorators' own isolated per-class registrations.

## Methods

### `execute` / `execute_async`

```python
schema.execute(query: str | None, *, variable_values=None, context=None, root_value=None, operation_name=None,
               resolved_dependencies=None, document=None) -> dict
```

Runs `query` and returns a spec-shaped `{"data": ..., "errors": [...]}`
response. `execute` is a synchronous convenience wrapper -- it can't be
called from inside an already-running event loop; use `execute_async` there
instead. See [Queries](../general/queries.md).

`document=` takes a prepared [persisted-query](../guides/persisted-queries.md)
document, skipping parsing and validation for a query that was already
registered; `query` may then be `None`. The same parameter is accepted by
`execute_incremental` and `subscribe_async`.

### `execute_incremental`

```python
schema.execute_incremental(query: str | None, *, variable_values=None, context=None, root_value=None,
                           operation_name=None, resolved_dependencies=None, document=None) -> AsyncGenerator[dict, None]
```

Runs a query/mutation operation using `@defer`/`@stream`, yielding the
initial payload followed by zero or more incremental patches. Async-only.
See [`@defer` and `@stream`](defer-and-stream.md).

### `subscribe_async`

```python
schema.subscribe_async(query: str | None, *, variable_values=None, context=None, root_value=None,
                       operation_name=None, resolved_dependencies=None, document=None) -> AsyncGenerator[dict, None]
```

Runs a subscription operation, yielding one response per source event.
Async-only. See [Subscriptions](../general/subscriptions.md).

### `validate_query`

```python
schema.validate_query(query: str, *, operation_name: str | None = None) -> None
```

Validates `query` against the compiled schema, raising `bramble.GraphQLError`
on the first violation found (an unknown field, a type mismatch, ...).
Returns `None` if valid -- useful for validating a query ahead of execution,
e.g. in a CI check against committed `.graphql` files.

### `resolve_persisted_query`

```python
schema.resolve_persisted_query(sha256_hash: str, *, query: str | None = None, operation_name: str | None = None) -> bool
```

Implements the Automatic Persisted Queries protocol against this schema's
own cache, returning whether the hash was already cached.

### `prepare_persisted_query`

```python
schema.prepare_persisted_query(sha256_hash: str, *, query: str | None = None, operation_name: str | None = None)
```

The same protocol, but returns an object with `.cache_hit` and `.document` --
pass `.document` to an execute method to run it without re-parsing or
re-validating. This is what makes a cache hit actually cheaper. See
[Persisted queries](../guides/persisted-queries.md).

### `to_sdl`

```python
schema.to_sdl() -> str
```

Renders the schema's GraphQL SDL. Also available as `str(schema)`. See
[Schema basics](../general/schema-basics.md#rendering-sdl).

## Inspecting a built schema

A few dictionaries populated while building the schema are useful for
introspection/tooling beyond just SDL rendering:

- `schema.types_by_name: dict[str, type]` -- every reachable type, keyed by
  its GraphQL name.
- `schema.implementors_by_interface: dict[str, list[type]]` -- an
  interface's name to the list of classes implementing it.
- `schema.unions_by_name`, `schema.union_members_by_name` -- registered
  unions and their member classes.
