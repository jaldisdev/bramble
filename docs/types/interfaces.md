# Interfaces

`@bramble.interface` declares an interface type. An implementing type
inherits from it directly -- there's no separate `implements=[...]` list to
keep in sync, since Python's own inheritance already gives every
implementor the interface's fields:

```python
import bramble

@bramble.interface
class Node:
    @bramble.field
    def id(parent: bramble.Parent[object]) -> bramble.ID:
        return parent.id  # type: ignore[attr-defined]

@bramble.type
class Author(Node):
    @bramble.field
    def name(parent: bramble.Parent["AuthorRecord"]) -> str:
        return parent.name

    @classmethod
    def is_type_of(cls, obj: object, info: bramble.Info) -> bool:
        return isinstance(obj, AuthorRecord)

@bramble.type
class Post(Node):
    @bramble.field
    def title(parent: bramble.Parent["PostRecord"]) -> str:
        return parent.title

    @classmethod
    def is_type_of(cls, obj: object, info: bramble.Info) -> bool:
        return isinstance(obj, PostRecord)
```

```graphql
interface Node {
  id: ID!
}

type Author implements Node {
  id: ID!
  name: String!
}

type Post implements Node {
  id: ID!
  title: String!
}
```

## Resolving the concrete type

A field typed as the interface (`Node` above) can return a value of any
implementing type -- bramble figures out which one at execution time by
trying each implementor's own `is_type_of(obj, info)` classmethod, falling
back to a plain `isinstance(obj, implementor)` check for an implementor
that doesn't define one:

```python
@bramble.type
class Query:
    @bramble.field
    def node(id: bramble.ID, info: bramble.Info) -> Node | None:
        database = info.context
        return database.authors.get(id) or database.posts.get(id)
```

```python
schema.execute('{ node(id: "p1") { __typename id ... on Post { title } } }')
# {'data': {'node': {'__typename': 'Post', 'id': 'p1', 'title': 'Hello GraphQL'}}}
```

### Type resolution is synchronous

`is_type_of` is called synchronously and cannot be `async` -- the same holds for
a union's `resolve_type`. This is a deliberate constraint, not an oversight: it
runs once per resolved abstract value, on the hot path, so permitting I/O there
would let an N+1 query hide somewhere very few people think to look.

When the concrete type depends on something not already on the value, fetch it
in the resolver that produced the value rather than in the hook -- select the
discriminating column alongside everything else, and let `is_type_of` read it.
That keeps the decision a pure function of the value it is handed.

### Deciding once, on the interface

`is_type_of` may answer in either of two ways. The per-implementor form above
returns a boolean -- "is the value one of me?". Alternatively it may return the
concrete type itself, which lets a single hook on the shared interface decide
for every implementor at once:

```python
@bramble.interface
class Shape:
    id: bramble.ID

    def is_type_of(instance, *args, **kwargs) -> type:
        return Square if getattr(instance, "side", None) else Circle


@bramble.type
class Square(Shape):
    side: int


@bramble.type
class Circle(Shape):
    radius: int
```

Every implementor inherits that one method, and the returned type names the
answer directly. This is often the clearer shape when the decision is a single
branch over a discriminator -- a row's type column, say -- rather than a
property each type can test about itself.

Returning `None` means "no match", exactly as `False` does. The two forms may
be mixed across an interface's implementors.

A union has no shared base to hang such a hook on, so it takes the equivalent
callback on the union itself -- see [Unions](union.md) and its `resolve_type`.

Exactly one implementor must match a resolved value -- zero matches or more
than one both raise `bramble.GraphQLError` with
`code=ErrorCode.INTERFACE_TYPE_RESOLUTION_FAILED`, rather than silently
guessing.

## Registering implementors

bramble finds a type automatically when it appears somewhere in the schema as
a **field's return type**, a **resolver or directive argument**, or a **union
member** -- and registering an implementor that way registers its interfaces
along with it. So in the example above, `Author` and `Post` need no special
treatment as long as something else in the schema mentions them (a
`posts: [Post!]!` field, a `SearchResult` union, and so on).

The one case that needs help is an implementor reachable *only* through the
interface -- never named by any other field, argument, or union. bramble
discovers types by walking annotations, and in that shape nothing anywhere
names the concrete type, so there's nothing to walk to. List it in
`Schema(types=[...])` explicitly:

```python
schema = bramble.Schema(query=Query, types=[Author, Post])
```

Without it the implementor is missing from the schema entirely: it never
appears in `to_sdl()`, and a query using an inline fragment against it fails
validation with `inline fragment targets unknown type 'Author'`.

This most often comes up with a Relay-style `node(id: ID!): Node` field,
where concrete types are only ever reached through inline fragments. A
schema whose object types are also returned from ordinary fields generally
needs no `types=[...]` at all -- see
[`examples/blog`](../../examples/blog/schema.py), which has a `Node`
interface with two implementors and doesn't use it.

## Interface inheritance

An interface can itself inherit from another interface -- bramble collects
every level's fields into the implementor:

```python
@bramble.interface
class Error:
    message: str

@bramble.interface
class FieldError(Error):
    field: str

@bramble.type
class PasswordTooShort(FieldError):
    min_length: int
```

`PasswordTooShort` ends up with `message`, `field`, and `min_length`.

## Field covariance rules

An implementor is checked against each interface field it inherits:

- It cannot weaken a non-null interface field to nullable.
- It cannot add a new *required* argument the interface field doesn't
  declare (a new optional argument, with a default or itself nullable, is
  fine).

Violating either raises `bramble.SchemaError` when the schema is built.
Since implementors literally inherit the interface's fields, outright
*omitting* a field is not possible to begin with.
