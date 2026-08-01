# Schema directives

A schema directive is declarative metadata attached to a type, field, or
the schema itself, rendered into SDL -- unlike an
[operation directive](operation-directives.md), it carries no runtime
behavior of its own. `@bramble.schema_directive` declares one as a
dataclass-like class:

```python
import bramble
from bramble.schema_directive import Location

@bramble.schema_directive(locations=[Location.FIELD_DEFINITION])
class Auth:
    role: str
```

Apply it via a field or type's `directives=`:

```python
@bramble.type
class Post:
    @bramble.field(directives=[Auth(role="admin")])
    def internal_notes(parent: bramble.Parent["PostRecord"]) -> str:
        return "flagged for editorial review"
```

```graphql
type Post {
  internalNotes: String! @auth(role: "admin")
}

directive @auth(role: String!) on FIELD_DEFINITION
```

The field still resolves completely normally -- `@auth` is purely
declarative here. Enforcing an actual permission check based on it would be
done in the resolver itself (or a wrapper around it), not by the directive
mechanism.

## Locations

`Location` covers every *type-system* directive location: `SCHEMA`,
`SCALAR`, `OBJECT`, `FIELD_DEFINITION`, `ARGUMENT_DEFINITION`, `INTERFACE`,
`UNION`, `ENUM`, `ENUM_VALUE`, `INPUT_OBJECT`, `INPUT_FIELD_DEFINITION`.
Applying a directive somewhere its declared `locations` doesn't include
raises `bramble.SchemaError` when the schema is built.

Directives can be applied at the type level (`@bramble.type(directives=[...])`),
the field level (`@bramble.field(directives=[...])`), the argument level
(`Annotated[T, bramble.argument(directives=[...])]`), and the scalar level
(`bramble.scalar(directives=[...])`).

## Directive fields

A schema directive's own fields are declared like a dataclass; use
`bramble.directive_field(name=..., default=...)` to override a field's
GraphQL-facing name (useful for a name that collides with a Python
keyword, like `from`):

```python
from bramble.schema_directive import directive_field

@bramble.schema_directive(locations=[Location.FIELD_DEFINITION])
class Override:
    from_: str = directive_field(name="from")
```

```graphql
directive @override(from: String!) on FIELD_DEFINITION
```

## `repeatable`

Pass `repeatable=True` to allow the same directive to be applied more than
once to the same location:

```python
@bramble.schema_directive(locations=[Location.OBJECT], repeatable=True)
class Key:
    fields: str
```

```graphql
directive @key(fields: String!) repeatable on OBJECT
```

See [Federation](../federation/introduction.md) for a full set of
production schema directives (`@key`, `@shareable`, `@external`, ...) built
this exact way.
