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

## Locations

`DirectiveLocation` covers the *executable* directive locations (where a
directive can appear inside a query document): `QUERY`, `MUTATION`,
`SUBSCRIPTION`, `FIELD`, `FRAGMENT_DEFINITION`, `FRAGMENT_SPREAD`,
`INLINE_FRAGMENT`. Applying a directive somewhere its declared `locations`
doesn't include is a validation error.

## Chaining

Multiple directives on the same field (`@repeat(times: 2) @shout`) apply in
sequence -- each one's result feeds into the next.
