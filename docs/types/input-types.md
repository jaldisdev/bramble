# Input types

`@bramble.input` declares a type usable as an argument, rather than as a
field's return type -- an ordinary dataclass with no resolver fields:

```python
import bramble

@bramble.input
class PostFilter:
    author_id: str | None = None
    published_after: str | None = None

@bramble.type
class Query:
    @bramble.field
    def posts(filter: PostFilter | None = None) -> list["Post"]:
        ...
```

```graphql
input PostFilter {
  authorId: String
  publishedAfter: String
}
```

A query supplying this argument sends a GraphQL input object literal (or an
equivalent JSON object via a variable), and the resolver receives a real
`PostFilter` instance -- not a raw `dict` -- constructed from it:

```python
schema.execute('{ posts(filter: {authorId: "a1"}) { title } }')
```

An `@bramble.input`-decorated class cannot declare a resolver field
(`@bramble.field`-backed) -- every field must be a plain, user-suppliable
value; attempting one raises `bramble.SchemaError` at decoration time.

## `@oneOf` input types

Pass `one_of=True` to require that exactly one field of the input be set --
rendered as the `@oneOf` directive in SDL, per the GraphQL `oneOf` input
objects proposal:

```python
@bramble.input(one_of=True)
class SearchFilter:
    by_id: int | None = None
    by_name: str | None = None
```

```graphql
input SearchFilter @oneOf {
  byId: Int
  byName: String
}
```

## Defaults

A plain attribute default becomes the input field's own default value, used
whenever a query omits the argument entirely:

```python
@bramble.input
class Pagination:
    limit: int = 20
    offset: int = 0
```

Use `dataclasses.field(default_factory=...)` (or `bramble.field(default_factory=...)`)
for a default that shouldn't be shared between instances (a mutable
default, like an empty list).
