# Operation directives

An operation directive is applied inside a query document (`@skip`,
`@include`, or a custom one) and can transform an already-resolved field
value at execution time. `@skip`/`@include` are built in; a custom one is
declared with `@bramble.directive`:

```python
import bramble
from bramble.directive import DirectiveLocation, DirectiveValue

@bramble.directive(locations=[DirectiveLocation.FIELD], description="Uppercases a resolved string value")
def shout(value: DirectiveValue[str]) -> str:
    return value.upper()
```

Register it on the schema via `Schema(directives=[shout])`, then use it in
a query:

```python
schema = bramble.Schema(query=Query, directives=[shout])
schema.execute("{ posts { title @shout } }")
# {'data': {'posts': [{'title': 'HELLO GRAPHQL'}]}}
```

## `DirectiveValue[T]`

Annotate exactly one parameter `DirectiveValue[T]` to receive the field's
already-resolved value -- the directive function's other parameters (if
any) become the directive's own GraphQL arguments, the same way a
resolver's non-`Parent`/`Info` parameters become field arguments:

```python
@bramble.directive(locations=[DirectiveLocation.FIELD])
def repeat(value: DirectiveValue[str], times: int = 2) -> str:
    return value * times
```

```graphql
{ greeting @repeat(times: 3) }
```

A directive function can be `async def` -- bramble awaits it like any other
resolver.

## `Info` and `Depends[T]`

A directive function supports the same `Info`/`Annotated[T, bramble.Depends(...)]`
injection a resolver does -- both go through the identical classifier, so
`Info`/`Depends[T]` parameters are excluded from the directive's own
GraphQL arguments exactly like they are for a resolver's field arguments:

```python
@bramble.directive(locations=[DirectiveLocation.FIELD])
def audit_log(value: DirectiveValue[str], info: bramble.Info) -> str:
    print(f"resolved {info.field_name}")
    return value
```

See [Dependency injection](dependency-injection.md) for `Depends[T]`'s full
reference.

## Locations

`DirectiveLocation` covers the *executable* directive locations (where a
directive can appear inside a query document): `QUERY`, `MUTATION`,
`SUBSCRIPTION`, `FIELD`, `FRAGMENT_DEFINITION`, `FRAGMENT_SPREAD`,
`INLINE_FRAGMENT`. Applying a directive somewhere its declared `locations`
doesn't include is a validation error.

## Chaining

Multiple directives on the same field (`@repeat(times: 2) @shout`) apply in
sequence -- each one's result feeds into the next.

## Reading a field's directives before it resolves

A directive function transforms a value the resolver has already produced.
That is the wrong moment for a directive whose job is to *influence* the
fetch -- one carrying an auth token, a content language, a tenant. For
those, read `Info.field_directives` instead: the directives written on the
field currently being resolved, available before the resolver runs.

```python
@bramble.directive(locations=[DirectiveLocation.FIELD])
def in_context(value: DirectiveValue[Page], language: str) -> Page:
    return value  # nothing to do after the fact; the work happens below

@bramble.field
def page(info: bramble.Info, slug: str) -> Page:
    language = "en"
    for directive in info.field_directives:
        if directive.name == "inContext":
            language = directive.arguments["language"]
    return load_page(slug, language=language)
```

Each entry is a `bramble.FieldDirective` with `.name` (the GraphQL
directive name) and `.arguments`, keyed by the directive function's own
parameter names and coerced through their declared types -- so an enum
arrives as the Python member, an input object as a real instance. The
tuple is in the order the query wrote them, and it is empty for a field
with no directives.

`@skip`/`@include` never appear: they are applied structurally while the
query is lowered, so a skipped field is simply absent. Neither do
`@defer`/`@stream`.

The same attribute is available from a
[schema extension](../guides/extensions.md)'s `resolve` hook, which is
where this belongs when several fields need the same treatment:

```python
class LanguageExtension(bramble.SchemaExtension):
    def resolve(self, next_, source, info, **kwargs):
        for directive in info.field_directives:
            if directive.name == "inContext":
                info.context.language = directive.arguments["language"]
        return next_(source, info, **kwargs)
```
