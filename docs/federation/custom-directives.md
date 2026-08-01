# Federation directives

`bramble.federation` exposes every Apollo Federation v2 directive as a
`@bramble.schema_directive`-decorated class, ready to apply directly via
`bramble.type`/`bramble.field`'s own `directives=`, or via
`federation.type(...)`'s shorthand keywords for the common ones (`keys`,
`shareable`, `inaccessible`, `tags`, `interface_object` -- see
[Federation](introduction.md#federationtype)). Any directive without its
own shorthand keyword is applied the same way any other schema directive
is, via `directives=`/`extra_directives=`:

```python
from bramble.federation import Requires, Provides, External, Override

@bramble.type
class Product:
    id: str

    @bramble.field(directives=[External()])
    def weight(parent: bramble.Parent["Product"]) -> float:
        ...

    @bramble.field(directives=[Requires(fields="weight")])
    def shipping_estimate(parent: bramble.Parent["Product"]) -> float:
        ...
```

## Directive reference

- **`Key(fields, resolvable=True)`** -- marks an entity's identifying
  field(s). See [Entities](entities.md).
- **`Shareable()`** -- allows a field to be resolved by more than one
  subgraph.
- **`External()`** -- marks a field as owned by another subgraph, present
  here only for reference by `@requires`/`@provides`/`@key`.
- **`Requires(fields)`** -- declares that resolving this field needs other
  (external) fields already resolved first.
- **`Provides(fields)`** -- declares that resolving this field also
  incidentally resolves some of its return type's own fields, letting the
  gateway skip a separate round trip for them.
- **`Override(from_, label=None)`** -- claims resolution of a field away
  from another named subgraph. `from_` renders as `from` in SDL (see
  [Schema directives](../types/schema-directives.md#directive-fields) for
  why a trailing underscore is needed here).
- **`Inaccessible()`** -- hides a type/field from the public supergraph
  schema without removing it from this subgraph.
- **`Tag(name)`** -- an arbitrary label, usable for schema composition
  rules (e.g. contracts); repeatable.
- **`InterfaceObject()`** -- marks a type as this subgraph's contribution
  to an interface defined elsewhere, without implementing it directly here.
- **`ComposeDirective(name)`** -- tells the composition step to preserve a
  custom directive into the supergraph schema.
- **`Authenticated()`** -- marks a type/field as requiring an authenticated
  request (enforcement is the gateway/router's job, not bramble's).
- **`RequiresScopes(scopes)`** -- marks a type/field as requiring specific
  OAuth2 scopes; `scopes` is a list of lists (each inner list is one
  sufficient set of scopes -- an "OR of ANDs").
- **`Policy(policies)`** -- same shape as `RequiresScopes`, for an
  authorization-policy based check instead.
- **`Link(url, import_=None)`** -- applied automatically by
  `federation.Schema` itself for the federation spec's own `@link`; rarely
  applied manually.

Every directive here is a real `@bramble.schema_directive` -- purely
declarative, rendered into SDL for the gateway/router to act on. None of
them enforce anything at bramble's own execution time (see
[Authentication](../guides/authentication.md) for how to actually enforce
access control in a resolver).

## `@extends`

The v1-era `extend type` / `@extends` style of declaring an entity's fields
across subgraphs is not supported -- bramble's SDL renderer has no
`extend type` concept to pair it with. Declare every field of a type
directly on that type's own class, using `@key`/`@external`/`@requires`/
`@provides` to describe cross-subgraph field ownership instead, which is
the Federation v2-native way regardless.
