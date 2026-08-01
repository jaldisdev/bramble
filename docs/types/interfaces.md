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

Exactly one implementor must match a resolved value -- zero matches or more
than one both raise `bramble.GraphQLError` with
`code=ErrorCode.INTERFACE_TYPE_RESOLUTION_FAILED`, rather than silently
guessing.

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
