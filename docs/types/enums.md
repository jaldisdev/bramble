# Enums

`@bramble.enum` declares a Python `enum.Enum` subclass as a GraphQL enum type:

```python
import enum
import bramble

@bramble.enum
class Color(enum.Enum):
    RED = "red"
    GREEN = "green"
    BLUE = "blue"
```

```graphql
enum Color {
  RED
  GREEN
  BLUE
}
```

A member's **GraphQL name is its Python identifier**, not its value -- `RED`, not `"red"`. That
matches how a GraphQL enum travels over the wire (by name), and leaves the value as a private
Python detail your resolvers can use however they like. The class stays an ordinary
`enum.Enum`, so `Color.RED.value`, `Color("red")` and `Color["RED"]` all keep working.

## Using an enum

An enum works as a field's return type, an argument, a list element, and inside an input type:

```python
@bramble.input
class PostFilter:
    colors: list[Color] | None = None

@bramble.type
class Query:
    @bramble.field
    def favourite() -> Color:
        return Color.RED

    @bramble.field
    def paint(color: Color) -> str:
        return f"painting with {color.value}"

    @bramble.field
    def search(filter: PostFilter) -> list[str]:
        ...
```

```python
schema.execute("{ favourite }")
# {'data': {'favourite': 'RED'}}

schema.execute("{ paint(color: GREEN) }")
# {'data': {'paint': 'painting with green'}}
```

A resolver receives the **real Python member** (`Color.GREEN`), not the name string -- bramble
coerces the incoming value before the resolver is called, recursing through lists and input
object fields the same way custom scalars do. On the way out, a returned member is serialized
back to its GraphQL name.

Enum values in a query are written **unquoted**: `paint(color: GREEN)`, never
`paint(color: "GREEN")`. A quoted value is a `String` literal, which the spec keeps distinct from
an enum value, and bramble rejects it at validation.

## Customising a member

`bramble.enum_value(...)` overrides a single member's GraphQL name or attaches a description,
deprecation reason, or directives. It stands in for the member's value; the real value is
restored onto the member afterwards, so `Status.OPEN.value` is unaffected:

```python
@bramble.enum
class Status(enum.Enum):
    OPEN = bramble.enum_value("open", description="Not yet started")
    IN_PROGRESS = "in-progress"
    LEGACY_DONE = bramble.enum_value("done", name="DONE", deprecation_reason="use COMPLETED")
    COMPLETED = "completed"
```

```graphql
enum Status {
  """Not yet started"""
  OPEN
  IN_PROGRESS
  DONE @deprecated(reason: "use COMPLETED")
  COMPLETED
}
```

Note that once a member declares `name=`, that override *is* its GraphQL name -- a query using
the Python identifier (`LEGACY_DONE` above) is rejected as an unknown value, the same as any
other name the enum doesn't declare.

`bramble.enum_value(...)` accepts:

- **`value`** -- the member's real Python value (positional, required).
- **`name`** -- the GraphQL name, overriding the Python identifier.
- **`description`** -- rendered above the member in SDL.
- **`deprecation_reason`** -- renders `@deprecated(reason: "...")`.
- **`directives`** -- applied [schema directives](schema-directives.md) at the `ENUM_VALUE`
  location.

## Enum-level options

`@bramble.enum` itself takes `name`, `description`, and `directives` (validated against the
`ENUM` location):

```python
@bramble.enum(name="Shade", description="A shade of grey", directives=[Internal()])
class Grey(enum.Enum):
    LIGHT = "light"
    DARK = "dark"
```

## Naming is left alone

Unlike fields and arguments, enum members are **not** run through
[`auto_camel_case`](schema-configurations.md#auto_camel_case). GraphQL enum members are
conventionally `SCREAMING_SNAKE_CASE`, which is already how they're written in Python, so
camelCasing `IN_PROGRESS` would corrupt it. Use `enum_value(name=...)` if you want a different
name for a specific member.

## Validation

An invalid enum value is caught at validation time, before any resolver runs:

```python
schema.validate_query("{ paint(color: MAUVE) }")
# bramble.GraphQLError: 'MAUVE' is not a valid value for enum 'Color'
```

This applies one level down too -- an invalid enum inside an input object literal is caught the
same way.
