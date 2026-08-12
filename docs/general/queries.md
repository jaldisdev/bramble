# Queries

A query field is any `@bramble.field`-decorated method (or function) on the
`query` root type, or on any object type reachable from it. bramble accepts
either a **method-style** resolver (the field's containing class instance is
never passed implicitly -- see [No self/root magic](../types/resolvers.md))
or a **plain function**, and both a nested object's own fields and the root
query's fields work exactly the same way:

```python
import bramble

@bramble.type
class Author:
    @bramble.field
    def name(parent: bramble.Parent["AuthorRecord"]) -> str:
        return parent.name

@bramble.type
class Query:
    @bramble.field
    def author(id: str) -> Author:
        return Author.from_record(lookup(id))
```

## Arguments

A resolver's plain (non-`Parent`/`Info`) parameters become the field's
GraphQL arguments, keyed by name (camelCased by default, same as fields):

```python
@bramble.type
class Query:
    @bramble.field
    def greet(name: str = "world") -> str:
        return f"Hello, {name}!"
```

```graphql
type Query {
  greet(name: String!): String!
}
```

A parameter with a default value becomes an optional argument; one without
becomes required. The default is published in the schema, so clients and
codegen tools see it too:

```python
@bramble.field
def search(limit: int = 10) -> str: ...
```

```graphql
type Query {
  search(limit: Int! = 10): String!
}
```

Note that the argument keeps its non-null type: `Int! = 10` means "an
integer is required *if* you pass one, but you may omit it". A default whose
value has no GraphQL literal spelling (an arbitrary Python object) is left
out of the SDL rather than guessed at; the argument is still optional.

Use `typing.Annotated` with `bramble.argument(...)` to
override an argument's own GraphQL name, attach a deprecation reason, or
apply directives, independently of the Python parameter name:

```python
from typing import Annotated

@bramble.type
class Query:
    @bramble.field
    def greet(
        name: Annotated[str, bramble.argument(name="who", deprecation_reason="use 'who' instead")] = "world",
    ) -> str:
        return f"Hello, {name}!"
```

See [Resolvers](../types/resolvers.md) for the full `Parent`/`Info`
injection reference, and [Input types](../types/input-types.md) for
accepting a structured object as a single argument.

## Executing a query

```python
schema = bramble.Schema(query=Query)

result = schema.execute("{ greet(name: \"Ada\") }")
# {'data': {'greet': 'Hello, Ada!'}}
```

Pass `variable_values`, `context`, `root_value`, and `operation_name` as
keyword arguments to `execute`/`execute_async` for a query using GraphQL
variables, resolvers that need request-scoped state (a database connection,
the authenticated user, ...), or a document containing more than one named
operation:

```python
result = schema.execute(
    "query Greet($name: String!) { greet(name: $name) }",
    variable_values={"name": "Ada"},
    context=my_database_connection,
    operation_name="Greet",
)
```

Inside a resolver, `context` and the GraphQL variables are reachable through
an injected `Info` parameter -- see
[Accessing execution context](../types/resolvers.md#info).

## Errors

Raise `bramble.GraphQLError` from inside a resolver to surface a field-level
error in the response's `"errors"` list (per spec, the failed field's own
position in `"data"` becomes `null`, bubbling up to the nearest nullable
ancestor):

```python
@bramble.type
class Query:
    @bramble.field
    def user(id: str) -> "User":
        record = database.users.get(id)
        if record is None:
            raise bramble.GraphQLError(f"no such user '{id}'", code=bramble.ErrorCode.ARGUMENT_TYPE_MISMATCH)
        return User.from_record(record)
```

See [Exceptions](../types/exceptions.md) for the full `GraphQLError`/
`ErrorCode` reference.

## Validation

Every query is validated against the compiled schema before a single resolver
runs -- `execute`, `execute_async`, `execute_incremental`, and
`subscribe_async` all do it, and `Schema.validate_query(query)` runs the same
pass on its own. Validation happens in Rust, and raises `bramble.GraphQLError`
with the offending source location on the first violation found.

The rules enforced:

- **Fields exist** on the type they're selected from, and arguments are
  declared, unique, and type-check against their literal values.
- **Required arguments are present** -- an argument is required only if it is
  non-null *and* has no default.
- **Leaf fields are selected bare; composite fields need a selection set.**
  `{ user }` where `user` is an object type is an error, as is
  `{ name { length } }` where `name` is a `String`.
- **Fragments target real types they could actually apply to.** Spreading a
  fragment on `Post` inside a `User` selection can never match anything, so
  it's rejected rather than silently ignored.
- **Fragment spreads form no cycles.** `fragment A on T { ...A }` and mutually
  recursive pairs are rejected outright.
- **Variables are declared uniquely**, and directives are used only at
  locations their declaration allows.
- **A subscription selects exactly one root field**, whenever that's decidable
  without knowing variable values.

Two rules are deliberately not enforced, and are worth knowing about:

- **Variable *usage* types aren't checked.** An argument given a variable of
  the wrong type passes validation and fails at execution instead, because a
  variable's coerced type isn't known without full variable-definition
  coercion.
- **Duplicate operation and fragment names aren't detected.** The parser keys
  both by name internally, so a redefinition is collapsed before validation
  ever sees the document.
