# Federation

`bramble.federation` implements Apollo Federation v2, turning a bramble
schema into a subgraph a federation gateway can compose into a larger
supergraph. Only two imports are actually needed for a typical subgraph:
`bramble.federation.type` (sugar for applying federation directives) and
`bramble.federation.Schema` (a drop-in replacement for `bramble.Schema`).
Everything else the spec requires -- `_service { sdl }`, `_entities(...)`,
the `_Any`/`FieldSet` scalars, and the subgraph's own `@link` declaration --
is synthesized automatically.

```python
import bramble
import bramble.federation as federation

@federation.type(keys=["id"])
class Product:
    id: str
    name: str
    price: float

    @classmethod
    async def resolve_reference(cls, id: str, info: bramble.Info) -> "Product | None":
        catalog = info.context
        record = catalog.products.get(id)
        if record is None:
            return None
        return cls(id=record.id, name=record.name, price=record.price)

@bramble.type
class Query:
    @bramble.field
    def product(id: str, info: bramble.Info) -> Product | None:
        catalog = info.context
        record = catalog.products.get(id)
        if record is None:
            return None
        return Product(id=record.id, name=record.name, price=record.price)

schema = federation.Schema(query=Query, types=[Product])
```

See [`examples/federation_products/schema.py`](../../examples/federation_products/schema.py)
for the complete, runnable version of this example.

## `federation.Schema`

A real subclass of `bramble.Schema` (not a delegating wrapper) -- it takes
the same `query`/`mutation`/`subscription`/`types`/`config`/... arguments,
plus:

- **`federation_version`** -- default `"2.6"`; used in the subgraph's own
  `@link(url: "https://specs.apollo.dev/federation/v<version>")`.

Building a `federation.Schema` adds two synthetic fields to `query`:

- `_service: _Service!` (with one field, `sdl: String!`) -- returns this
  subgraph's own rendered SDL, exactly what a gateway needs to compose it.
- `_entities(representations: [_Any!]!): [_Entity]!` -- resolves a batch of
  entity references from another subgraph. Only added if at least one type
  in `types=` carries an `@key` directive (see [Entities](entities.md)); the
  `_Entity` union is built automatically over every such type.

It also registers the `_Any`/`FieldSet` scalars (see
[`bramble/federation/scalars.py`](../../bramble/federation/scalars.py)) and
applies the subgraph's own `@link` schema directive, with an `import` list
covering whichever federation directives actually got used somewhere in the
schema.

## `federation.type`

Sugar over `bramble.type(directives=[...])` for the federation directives
commonly applied at the type level, so a subgraph doesn't need to import
and spell out `Key`/`Shareable`/etc. itself for the common case:

```python
federation.type(
    keys: Sequence[str] = (),
    shareable: bool = False,
    inaccessible: bool = False,
    tags: Sequence[str] = (),
    interface_object: bool = False,
    extra_directives: Sequence[object] = (),
)
```

```python
@federation.type(keys=["id"], shareable=True, tags=["public"])
class Product:
    id: str
    name: str
```

is equivalent to:

```python
@bramble.type(directives=[Key(fields="id"), Shareable(), Tag(name="public")])
class Product:
    id: str
    name: str
```

`extra_directives=` accepts any other applied directive instance directly,
for a federation directive `federation.type`'s own keyword arguments don't
have shorthand for (e.g. `Override`, `RequiresScopes`) -- see
[Custom directives](custom-directives.md).

## `resolve_reference`

An entity type's `resolve_reference` classmethod is called once per
representation in an incoming `_entities` request. The gateway supplies
only the type's own `@key` fields, so a real implementation looks the rest
up from a database/service rather than trusting any other fields on the
representation. Declaring an `info` parameter (by name) gets the same
`Info` a normal field resolver would -- for `info.context` database access,
exactly like any other resolver.
