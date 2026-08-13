# Exceptions

## `GraphQLError`

`bramble.GraphQLError` is the exception a resolver, operation directive, or
`resolve_type` callback raises for a problem the client should see as a
spec-shaped GraphQL error, rather than an unhandled server exception:

```python
raise bramble.GraphQLError(
    f"no such post '{post_id}'",
    code=bramble.ErrorCode.ARGUMENT_TYPE_MISMATCH,
)
```

Raised from inside a resolver, it becomes an entry in the response's
`"errors"` list, with the failed field's own position in `"data"` set to
`null` (bubbling up to the nearest nullable ancestor, per spec) rather than
aborting the whole response:

```python
result = schema.execute('mutation { addComment(postId: "missing", body: "x") { id } }')
# result["data"] is None
# result["errors"][0]["message"] == "no such post 'missing'"
```

Constructor arguments:

- **`message`** -- required, the human-readable error message.
- **`code`** -- required, an `ErrorCode` member, rendered under
  `extensions.code` in the response.
- **`locations`** -- optional `list[tuple[int, int]]` of `(line, column)`
  positions in the source query.
- **`path`** -- optional `list[str | int]` response path.
- **`extensions`** -- optional `dict` merged alongside `code` under the
  error's own `extensions`. A `code` key here overrides the `code`
  argument, which is how you publish an application-specific code under
  the conventional key -- at the cost of bramble's own `ErrorCode` being
  absent from that error. Give the application code its own key if you
  want both.
- **`original_error`** -- optional; the exception this error wraps. See
  below.

A `GraphQLError` raised during execution has its `locations`/`path` filled
in automatically by bramble if not already set; one raised for a
request-level problem (a malformed query, a schema-build-time issue) may
have neither.

## `original_error`

A resolver may raise anything, not just a `GraphQLError`. Anything else
becomes a generic field error carrying the exception's message -- and the
exception itself stays reachable on `error.original_error`:

```python
class PermissionDenied(Exception): ...

@bramble.field
def secret() -> str:
    raise PermissionDenied("not yours")   # ordinary domain exception
```

```python
class UpdateStatusCode(bramble.SchemaExtension):
    STATUS = {PermissionDenied: 403, TwoFactorRequired: 401}

    def on_operation(self):
        yield
        for error in self.execution_context.errors:
            status = self.STATUS.get(type(error.original_error))
            if status is not None:
                self.execution_context.context.response.status_code = status
```

That is the point of it: service-layer code raises the exceptions it would
raise anywhere else, and the GraphQL layer decides how they surface. The
alternative -- raising `bramble.GraphQLError` with a marker in
`extensions` from deep inside domain code -- pushes transport vocabulary
somewhere it doesn't belong.

`original_error` is `None` for an error raised deliberately as a
`GraphQLError` (there is no other exception involved) and for one bramble
raised itself (a validation failure, a non-null violation). It is
Python-side only and never serialized: an internal exception's type and
repr stay off the wire unless you put them there yourself.

## `ErrorCode`

```python
class ErrorCode(enum.Enum):
    GRAPHQL_PARSE_FAILED
    GRAPHQL_VALIDATION_FAILED
    INTERFACE_TYPE_RESOLUTION_FAILED
    UNION_TYPE_RESOLUTION_FAILED
    UNKNOWN_FIELD
    UNKNOWN_ARGUMENT
    ARGUMENT_TYPE_MISMATCH
    INVALID_DIRECTIVE_LOCATION
    INVALID_FRAGMENT_TARGET
    PERSISTED_QUERY_NOT_FOUND
    PERSISTED_QUERY_MISMATCH
    FIELD_RESOLUTION_FAILED
```

Most of these are raised internally by bramble itself (parsing/validation
failures, interface/union resolution failures, persisted-query cache
misses); a resolver raising its own `GraphQLError` typically uses
`ARGUMENT_TYPE_MISMATCH` for "this input doesn't refer to anything real" or
picks whichever code best matches the situation for its own API's callers.

## `SchemaError`

`bramble.SchemaError` is raised for a problem with the schema *definition*
itself, at decoration/schema-build time -- an interface implementor missing
a required field, an `@bramble.input`-decorated class with a resolver
field, a directive applied somewhere its declared locations don't allow,
and so on. Since these are all caught before any query ever executes, a
`SchemaError` should never reach a running server's request-handling path
in practice -- it signals a bug in the schema's own Python code, not a bad
request.
