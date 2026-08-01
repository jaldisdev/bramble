# Schema basics

A bramble schema starts with a `Query` type -- every schema needs one. Types
are declared as `@bramble.type`-decorated classes; fields are either plain
dataclass attributes or resolver methods decorated with `@bramble.field`:

```python
import bramble

@bramble.type
class Query:
    @bramble.field
    def hello(name: str = "world") -> str:
        return f"Hello, {name}!"
```

`@bramble.type`-decorated classes are real Python dataclasses -- not a
parallel object model. `@bramble.field` marks an attribute as computed by a
resolver rather than user-supplied; a plain annotated attribute (no
`@bramble.field`) is an ordinary dataclass field instead, both for object
types your resolvers construct and for [input types](../types/input-types.md).

## Building a schema

`bramble.Schema` compiles a `query` root (and, optionally, `mutation` and
`subscription` roots) plus every type transitively reachable from them into
one validated schema:

```python
schema = bramble.Schema(query=Query)
```

A type that's never reached through any field's return type or resolver
argument (for example, an interface's implementor that's only ever
constructed but never returned from a field typed as the interface) needs to
be listed explicitly via `types=`:

```python
schema = bramble.Schema(query=Query, types=[Author, Post, Comment])
```

See [`examples/blog/schema.py`](../../examples/blog/schema.py) for a fuller
schema exercising interfaces, a union, custom scalars, schema/operation
directives, a mutation, and an async resolver all together, and
[Schema](../types/schema.md) for `Schema`'s full constructor and method
reference.

## Running a query

`Schema.execute` runs synchronously; `Schema.execute_async` is the async
equivalent (required if any resolver in the query's path is itself a
coroutine):

```python
result = schema.execute("{ hello }")
# {'data': {'hello': 'Hello, world!'}}
```

Both return a spec-shaped `{"data": ..., "errors": [...]}` dict --
`"errors"` is only present when at least one field failed. See
[Queries](queries.md) for more on resolvers and arguments,
[Mutations](mutations.md) for `mutation` roots, and
[Subscriptions](subscriptions.md) for `subscription` roots and
`Schema.subscribe_async`.

## Naming: camelCase by default

A field or argument with no explicit `name=` override is exposed to GraphQL
under a camelCase rendering of its Python identifier -- `get_user` becomes
`getUser`, `user_id` becomes `userId`:

```python
@bramble.type
class Query:
    @bramble.field
    def get_user(user_id: int) -> str:
        return f"user-{user_id}"

schema = bramble.Schema(query=Query)
schema.execute("{ getUser(userId: 1) }")
# {'data': {'getUser': 'user-1'}}
```

Set `SchemaConfig(auto_camel_case=False)` to keep raw Python identifiers
as-is instead. See [Schema configurations](../types/schema-configurations.md).

## Rendering SDL

`Schema.to_sdl()` (also available via `str(schema)`) renders the schema's
full GraphQL SDL -- every reachable type, union, and scalar, plus the
operation and schema directives actually in use:

```python
print(schema.to_sdl())
# schema {
#   query: Query
# }
#
# type Query {
#   hello(name: String!): String!
# }
```
