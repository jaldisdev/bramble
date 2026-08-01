# Object types

An object type is a plain Python class decorated with `@bramble.type`. It
becomes a real dataclass -- not a parallel object model -- so instances are
constructed, compared, and repr'd exactly like any other dataclass:

```python
import bramble

@bramble.type
class Author:
    name: str
    email: str
```

```graphql
type Author {
  name: String!
  email: String!
}
```

A plain annotated attribute (no `@bramble.field`) is an ordinary,
user-supplied dataclass field -- construct instances the normal way:

```python
author = Author(name="Ada Lovelace", email="ada@example.com")
```

## Resolver fields

A field can instead be *computed* by a resolver, using `@bramble.field`:

```python
@bramble.type
class Author:
    first_name: str
    last_name: str

    @bramble.field
    def full_name(parent: bramble.Parent["Author"]) -> str:
        return f"{parent.first_name} {parent.last_name}"
```

A resolver-backed field is excluded from the generated `__init__`/`__repr__`/
`__eq__` (there's nothing for a caller to pass in -- its value is always
computed at execution time), and it works with either a method-style
resolver (as above) or a plain function assigned to the attribute:

```python
def _full_name(parent: bramble.Parent["Author"]) -> str:
    return f"{parent.first_name} {parent.last_name}"

@bramble.type
class Author:
    first_name: str
    last_name: str
    full_name: str = bramble.field(resolver=_full_name)
```

See [Resolvers](resolvers.md) for the full `Parent`/`Info`/argument
injection reference.

## `field()` options

`bramble.field(...)` accepts:

- **`name`** -- overrides the field's GraphQL name (bypassing
  `auto_camel_case`).
- **`description`** -- rendered as a docstring above the field in SDL.
- **`directives`** -- a list of applied [schema directive](schema-directives.md)
  instances.
- **`default`** / **`default_factory`** -- a default value for a
  non-resolver field (mutually exclusive with each other, and with
  `resolver`).

```python
@bramble.type
class Query:
    @bramble.field(name="publicName", description="a public field")
    def internal() -> str:
        return "x"
```

```graphql
type Query {
  """a public field"""
  publicName: String!
}
```

## Lists and optional fields

A field's nullability and list-ness follow its Python type annotation
directly -- no separate wrapper type is needed:

```python
@bramble.type
class Author:
    name: str                    # String!
    nickname: str | None         # String
    books: list["Book"]          # [Book!]!
    awards: list[str] | None     # [String]
```

## `is_type_of`

An object type used as an interface implementor or union member can declare
a classmethod `is_type_of(cls, obj, info) -> bool` to control how bramble
picks which concrete type a resolved value corresponds to. Without it, a
plain `isinstance(obj, cls)` check is used instead -- see
[Interfaces](interfaces.md) and [Unions](union.md).

## Descriptions

A class docstring is not used as the type's GraphQL description; pass
`description=` to the decorator explicitly instead:

```python
@bramble.type(description="A person who writes posts")
class Author:
    name: str
```
