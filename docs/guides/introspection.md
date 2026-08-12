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

## Argument default values

`__InputValue.defaultValue` reports the argument's default as a GraphQL literal string, exactly as
the spec defines it -- the same literal SDL renders after `= `, so the two can't drift apart:

```python
@bramble.field
def search(limit: int = 10, term: str = "all", cursor: str | None = None) -> str: ...
```

```graphql
{ __type(name: "Query") { fields { args { name defaultValue } } } }
```

```json
[{"name": "limit",  "defaultValue": "10"},
 {"name": "term",   "defaultValue": "\"all\""},
 {"name": "cursor", "defaultValue": "null"}]
```

An argument with no default reports `null`, as does one whose default has no faithful GraphQL
spelling (an arbitrary Python object) -- reporting nothing beats reporting something wrong. The
argument stays optional at execution either way.

## Known gaps

- **`__Field.isDeprecated` is always `false`.** bramble has no field-level deprecation API --
  `bramble.field(...)` takes no `deprecation_reason`. Arguments and
  [enum values](../types/enums.md) *do* support deprecation, and introspection reports theirs
  accurately.
- **An input object field's own default is not reported.** Unlike a resolver argument, an
  `@bramble.input` field's default isn't carried in the schema IR, so `__InputValue.defaultValue`
  is `null` for input fields even when the Python attribute has a default.

Schema directives are reported with their name, locations, and repeatability, but without
argument types -- a schema directive's fields aren't carried with type information in the IR.

## Disabling introspection

bramble has no built-in switch for turning introspection off in production. If you need that, the
practical approach today is to reject queries containing `__schema`/`__type` at your HTTP layer
before calling `execute` -- for example by checking the incoming query string, or by subclassing
your integration's view and short-circuiting in `run()`. See
[Integrations](../integrations/index.md).
