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
becomes required. Use `typing.Annotated` with `bramble.argument(...)` to
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
