# Entities

An **entity** is a type another subgraph can reference and extend --
declared with one or more `@key` directives naming the field(s) that
uniquely identify it:

```python
@federation.type(keys=["id"])
class Product:
    id: str
    name: str
    price: float

    @classmethod
    async def resolve_reference(cls, id: str, info: bramble.Info) -> "Product | None":
        ...
```

```graphql
type Product @key(fields: "id", resolvable: true) {
  id: String!
  name: String!
  price: Float!
}
```

Every entity type must define `resolve_reference` (sync or async) --
called once per representation when a gateway sends this subgraph an
`_entities` request to resolve fields it doesn't own itself. Its
parameters mirror the `@key` fields by name (plus an optional `info`
parameter, injected the same way a normal resolver's would be):

```python
@classmethod
async def resolve_reference(cls, id: str, info: bramble.Info) -> "Product | None":
    catalog = info.context
    record = catalog.products.get(id)
    if record is None:
        return None
    return cls(id=record.id, name=record.name, price=record.price)
```

Returning `None` for a representation that can't be resolved is expected
and handled -- the corresponding entry in `_entities`'s own response list
is simply `null`.

## `@key` field constraints

`@key(fields="...")` currently only supports a flat, space-separated list
of top-level field names (e.g. `"id"`, `"id sku"`) -- a nested/braced
selection set (`"id { nested }"`) is not yet supported, and raises
`bramble.SchemaError` at schema-build time. Every named field must actually
exist on the type; a typo also raises `SchemaError` immediately rather than
failing silently at query time.

## Multiple keys

Applying `@key` more than once (it's `repeatable`) declares multiple
independent ways to reference the same entity -- pass more than one field
list to `keys=`:

```python
@federation.type(keys=["id", "sku"])
class Product:
    id: str
    sku: str
    name: str
```

```graphql
type Product @key(fields: "id", resolvable: true) @key(fields: "sku", resolvable: true) {
  id: String!
  sku: String!
  name: String!
}
```

## Non-resolvable keys

`@key`'s `resolvable` argument (default `true`) marks a key that only
exists to reference the entity from another subgraph, without this
subgraph being expected to resolve it -- `keys=[...]`'s shorthand always
sets `resolvable: true`; pass a `Key` instance directly via
`extra_directives=` for `resolvable=False`:

```python
from bramble.federation import Key

@federation.type(extra_directives=[Key(fields="id", resolvable=False)])
class Product:
    id: str
```

## The `_entities` field

`federation.Schema` builds `_entities(representations: [_Any!]!): [_Entity]!`
automatically, where `_Entity` is a union over every type in `types=` that
carries at least one `@key` directive -- there's nothing to configure here
beyond declaring the entity types themselves via `@key`/`federation.type(keys=...)`.

## `_service`

`federation.Schema` also adds `_service { sdl }`, returning this subgraph's
own rendered SDL (`Schema.to_sdl()`) -- exactly what a gateway calls to
compose this subgraph into the supergraph.
