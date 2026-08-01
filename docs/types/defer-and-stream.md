# `@defer` and `@stream`

`@defer` and `@stream` let a client receive part of a response after the
initial payload, instead of waiting for the entire query to resolve before
seeing anything.

- **`@defer`**, applied to a fragment spread or inline fragment, delivers
  that fragment's fields in a later patch instead of the initial response.
- **`@stream`**, applied to a list-typed field, delivers the list's items
  incrementally as they become available, instead of all at once.

Both are client-side query directives -- no schema changes are needed to
support them; any query against any schema can use them.

## `@defer`

```graphql
query {
  id
  ... @defer {
    author {
      name
    }
  }
}
```

Run with `Schema.execute_incremental` (not `execute`/`execute_async` --
see below) and iterate the async generator it returns:

```python
async for payload in schema.execute_incremental(query):
    print(payload)
# {'data': {'id': 'q1'}, 'hasNext': True}
# {'incremental': [{'data': {'author': {'name': 'Ada'}}, 'path': []}], 'hasNext': False}
```

## `@stream`

A `@stream`-eligible field's resolver must be an **async generator**,
written exactly like a subscription resolver, but annotated
`bramble.Streamable[T]` instead of `AsyncGenerator[T, None]` -- this is what
tells bramble the field's own GraphQL type is a list (`[T!]!`), not a bare
`T` the way a subscription's own top-level field unwraps to:

```python
import bramble

@bramble.type
class Query:
    @bramble.field
    async def numbers(upto: int) -> bramble.Streamable[int]:
        for i in range(upto):
            yield i
```

```graphql
query {
  numbers(upto: 3) @stream(initialCount: 1)
}
```

`initialCount` (default `0`) controls how many items are eagerly resolved
into the initial payload before the rest stream in as separate patches.

## Scope: field exclusivity

A field is only deferred if it's the *only* selection of that response key
at its selection-set level -- if a sibling selection (inside or outside the
deferred fragment) selects the same field without `@defer`, the field
resolves eagerly as part of the initial payload instead of being deferred.
This is a deliberate simplification: the full GraphQL spec's defer-aware
field-merging behavior (where only a field selected *exclusively* inside
one or more `@defer` fragments, consistently, is actually deferred) is not
implemented.

## Payload shape

`execute_incremental` yields the initial payload
(`{"data": ..., "hasNext": bool}`, with deferred subtrees omitted and
streamed lists truncated to `initialCount`), followed by zero or more patch
payloads:

- `{"incremental": [{"data": ..., "path": [...]}], "hasNext": bool}` for a
  resolved deferred fragment.
- `{"incremental": [{"items": [...], "path": [...]}], "hasNext": bool}` for
  newly-available streamed list items.

This is the simpler `path`/`data`/`hasNext` payload shape (rather than the
newer `pending`/`id`/`completed` tracking revision some other
implementations use) -- a deliberate, documented scope choice.

## Why a separate entry point

`execute_async` rejects a query/mutation using `@defer`/`@stream` outright,
raising `bramble.GraphQLError` pointing at `execute_incremental` instead --
a query with no incremental markers at all keeps `execute_async`'s existing
single-shot behavior completely unchanged, with zero overhead for that
overwhelmingly common case. `execute_incremental` is async-only, like
`subscribe_async`: an incremental delivery is as open-ended as a
subscription's own event stream, so there's no synchronous convenience
wrapper.

## Serving over HTTP

Every bramble HTTP integration serves an `@defer`/`@stream` query as a
`multipart/mixed; boundary="graphql"` streamed POST response automatically
-- no separate setup beyond what [Integrations](../integrations/index.md)
already describes. There is no `Accept: text/event-stream`/SSE-over-GET
delivery mode.
