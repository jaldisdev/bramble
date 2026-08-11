# Introspection

Every bramble schema is introspectable. The `__schema` and `__type` meta-fields are available on
the query root automatically -- there's nothing to enable:

```python
schema.execute("{ __schema { queryType { name } } }")
# {'data': {'__schema': {'queryType': {'name': 'Query'}}}}

schema.execute('{ __type(name: "Post") { name kind } }')
# {'data': {'__type': {'name': 'Post', 'kind': 'OBJECT'}}}
```

This is what powers the GraphiQL IDE bramble serves (documentation pane, autocomplete, validation
as you type), and what client tooling -- Apollo, Relay, `graphql-codegen` -- uses to fetch a
schema over HTTP.

The third meta-field, `__typename`, is available on every object, interface, and union type and
resolves to the concrete type's name:

```python
schema.execute('{ node(id: "p1") { __typename } }')
# {'data': {'node': {'__typename': 'Post'}}}
```

## What's exposed

The full standard introspection type system: `__Schema`, `__Type`, `__Field`, `__InputValue`,
`__EnumValue`, `__Directive`, and the `__TypeKind` enum. A client can walk the whole schema --
every type and its kind, field and argument types (including nested `NON_NULL`/`LIST` wrappers via
`ofType`), interfaces and their `possibleTypes`, union members, input object fields, enum values
with their deprecation state, and declared directives.

`enumValues`, `fields`, `args` and `inputFields` all accept the spec's `includeDeprecated`
argument (default `false`).

## Introspection and SDL

Names beginning with `__` are reserved by the spec and implicit in every schema, so
`Schema.to_sdl()` deliberately omits them -- you won't see `__Schema`/`__Type` declarations or the
`__schema`/`__type` fields in SDL output. That keeps exported SDL portable: another server reading
it back would reject a schema that tried to redefine reserved names.

Introspection reads from the same compiled schema SDL rendering does, so the two never disagree.

## Known gaps

Two pieces of introspection report less than the spec allows, both because bramble's own schema
layer doesn't carry the information:

- **`__Field.isDeprecated` is always `false`.** bramble has no field-level deprecation API --
  `bramble.field(...)` takes no `deprecation_reason`. Arguments and
  [enum values](../types/enums.md) *do* support deprecation, and introspection reports theirs
  accurately.
- **`__InputValue.defaultValue` is always `null`.** The schema IR records only *whether* an
  argument has a default, not the value, so there is nothing faithful to report (the same gap SDL
  rendering has for argument defaults).

Schema directives are reported with their name, locations, and repeatability, but without
argument types -- a schema directive's fields aren't carried with type information in the IR.

## Disabling introspection

bramble has no built-in switch for turning introspection off in production. If you need that, the
practical approach today is to reject queries containing `__schema`/`__type` at your HTTP layer
before calling `execute` -- for example by checking the incoming query string, or by subclassing
your integration's view and short-circuiting in `run()`. See
[Integrations](../integrations/index.md).
