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

## Reading applied directives at execution time

A schema directive has no behavior of its own, but you can give it some by
reading it back while a request runs -- checking a marker on the field
being resolved, or on the type it returns, and acting on it. `Schema`
exposes three accessors for that:

```python
schema.applied_directives_for_field(parent_type, python_name)  # -> (Auth(role="admin"),)
schema.applied_directives_for_type(graphql_type)               # -> (Perspective(scope="account"),)
schema.type_for(graphql_type)                                  # -> the decorated Python class
```

They return the live directive instances, in declaration order, so the
arguments they were applied with (`Auth(role="admin").role`) are readable
directly. `applied_directives_for_type` and `type_for` accept the
decorated class, a GraphQL type name, or the `GraphQLTypeInfo` that
`Info.return_type` carries -- `NonNull`/`List` wrapping is unwrapped, so
`[Post!]!` resolves to `Post`.

`Info` carries everything the lookups need, which makes this usable from a
[schema extension](../guides/extensions.md) applied across every field:

```python
class AuthDirectiveExtension(bramble.SchemaExtension):
    def resolve(self, next_, source, info, **kwargs):
        applied = (
            info.schema.applied_directives_for_field(info.parent_type, info.python_name)
            + info.schema.applied_directives_for_type(info.return_type)
        )
        for directive in applied:
            if isinstance(directive, Auth) and directive.role not in info.context.roles:
                raise bramble.GraphQLError("not allowed")
        return next_(source, info, **kwargs)
```

Note `info.python_name` rather than `info.field_name`: fields are matched
on the Python identifier, which keeps the lookup independent of
`auto_camel_case`. An unknown type or field reads back as an empty tuple
rather than raising -- these are lookups, not assertions. `type_for`
returns `None` for a scalar or a union, neither of which has a single
decorated class behind it.
