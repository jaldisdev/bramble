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

## Distinguishing "omitted" from "explicit null"

GraphQL treats a field the client left out and a field the client sent as
`null` as different things; Python's `None` collapses them. Use
`bramble.UNSET` as the default when that difference matters — a partial-update
mutation being the usual case, where "leave this alone" and "clear this" must
not be confused:

```python
@bramble.input
class UpdateUser:
    name: str | None = bramble.UNSET
    nickname: str | None = bramble.UNSET

@bramble.mutation
def update_user(input: UpdateUser) -> User:
    if input.nickname is not bramble.UNSET:
        # Provided — possibly as null, which means "clear it".
        user.nickname = input.nickname
    return user
```

| Client sends | Attribute value |
| --- | --- |
| `{}` | `bramble.UNSET` |
| `{nickname: null}` | `None` |
| `{nickname: "ada"}` | `"ada"` |

`UNSET` is a falsy singleton — compare with `is`, not `==`. A field defaulting
to it is optional in the schema, and no default is rendered in SDL, since
`UNSET` has no GraphQL literal spelling and printing one would claim a default
the server never applies.

### `Maybe[T]` — the same distinction in the type system

`UNSET` puts the distinction in a *default*; `bramble.Maybe[T]` puts it in the
*type*, so it survives into something a type checker understands:

```python
@bramble.input
class UpdateUser:
    nickname: bramble.Maybe[str] = None

@bramble.mutation
def update_user(input: UpdateUser) -> User:
    if input.nickname is not None:      # provided at all?
        user.nickname = input.nickname.value   # possibly None, meaning "clear it"
    return user
```

| Client sends | Attribute value |
| --- | --- |
| `{}` | `None` |
| `{nickname: null}` | `Some(None)` |
| `{nickname: "ada"}` | `Some("ada")` |

`Some` is truthy even when it wraps `None`, so `if input.nickname:` reads as
"was it provided". `Maybe[T]` also works on a resolver argument.

On the wire it is a plain nullable `T` — GraphQL has no separate type for the
distinction, which is why it has to live in the wrapper. No `= null` default
is rendered either: that would tell clients omission substitutes null, which
is precisely what `Maybe` guarantees it doesn't.
