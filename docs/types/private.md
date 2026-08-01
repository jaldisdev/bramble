# Private fields

`bramble.Private[T]` marks a field as excluded from the generated GraphQL
schema entirely -- it stays a normal Python attribute (still participates
in `__init__`/equality/`repr` like any other dataclass field), just
invisible to any query:

```python
import bramble

@bramble.type
class User:
    name: str
    password_hash: bramble.Private[str]
```

```graphql
type User {
  name: String!
}
```

`password_hash` is still a real field on `User` instances -- resolvers on
this type (or code elsewhere with a `User` instance in hand) can read it
freely; it just never appears in the schema, and a query can never select
it.

Combining `Private[T]` with an explicit `bramble.field(...)` (a resolver,
description, directives, ...) raises `bramble.SchemaError` at decoration
time -- a field excluded from the schema can't also carry schema-facing
configuration.
