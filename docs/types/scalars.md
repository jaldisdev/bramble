# Scalars

## Built-in scalars

These Python types map to a GraphQL scalar automatically -- no registration
needed to use them in a field or argument annotation:

| Python type          | GraphQL scalar |
| --------------------- | -------------- |
| `str`                  | `String`       |
| `bool`                 | `Boolean`      |
| `int`                  | `Int`          |
| `float`                | `Float`        |
| `bramble.ID`           | `ID`           |
| `datetime.datetime`    | `DateTime`     |
| `datetime.date`        | `Date`         |
| `datetime.time`        | `Time`         |
| `decimal.Decimal`      | `Decimal`      |
| `uuid.UUID`            | `UUID`         |

`bramble.ID` is a `typing.NewType` over `str`, matching GraphQL's own `ID`
semantics (serialized as a string, but semantically an opaque identifier
rather than free text):

```python
@bramble.type
class Query:
    @bramble.field
    def author(id: bramble.ID) -> "Author":
        ...
```

## Custom scalars

`bramble.scalar(...)` describes how to (de)serialize a scalar, registered
against a Python type via `SchemaConfig(scalar_map={...})`:

```python
from typing import NewType
import bramble
from bramble.schema.config import SchemaConfig

Slug = NewType("Slug", str)

def _slugify(title: str) -> str:
    return title.lower().replace(" ", "-")

@bramble.type
class Post:
    @bramble.field
    def slug(parent: bramble.Parent["PostRecord"]) -> Slug:
        return _slugify(parent.title)

@bramble.type
class Query:
    @bramble.field
    def post_by_slug(slug: Slug) -> "Post | None":
        ...

schema = bramble.Schema(
    query=Query,
    config=SchemaConfig(
        scalar_map={
            Slug: bramble.scalar(name="Slug", serialize=lambda value: value, parse_value=_slugify),
        }
    ),
)
```

```graphql
scalar Slug
```

`bramble.scalar(...)` accepts:

- **`name`** -- the GraphQL scalar name; falls back to the Python type's own
  `__name__` if omitted.
- **`description`** -- rendered as a docstring above `scalar X` in SDL.
- **`specified_by_url`** -- rendered as `@specifiedBy(url: "...")`.
- **`serialize`** -- converts a resolved Python value to its
  GraphQL-response-facing form (`Callable[[Any], Any]`).
- **`parse_value`** -- converts an incoming argument/variable value to the
  Python value a resolver receives.
- **`directives`** -- applied [schema directive](schema-directives.md)
  instances.

A scalar reference that's never registered in `scalar_map` still round-trips
correctly through execution (values pass through unchanged) -- registering
it only affects whether/how `scalar X` and its description appear in
`to_sdl()`'s output. `bramble.Upload` (see [File upload](../guides/file-upload.md))
follows this same identity-passthrough pattern.

## Custom scalar arguments and lists

Custom scalar coercion recurses into lists and input object fields -- a
`list[Slug]` argument or a `Slug` field nested inside an input type both
run through `parse_value` correctly, not just a scalar used directly as a
top-level argument.
