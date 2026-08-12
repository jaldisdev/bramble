# Persisted queries

bramble implements the Automatic Persisted Queries (APQ) protocol via
`Schema.resolve_persisted_query`, caching queries by their SHA-256 hash so
a client can send just the hash on subsequent requests instead of the full
query text:

```python
import hashlib

query_text = "query { greet(name: \"Ada\") }"
sha256_hash = hashlib.sha256(query_text.encode("utf-8")).hexdigest()

# First request: client sends the hash only -- not yet cached.
schema.resolve_persisted_query(sha256_hash)
# raises bramble.GraphQLError, code=PERSISTED_QUERY_NOT_FOUND

# Client resends with the full query text alongside the hash -- gets cached.
was_cache_hit = schema.resolve_persisted_query(sha256_hash, query=query_text)
# False -- this was a fresh parse+cache, not a hit

# Any later request with just the hash now succeeds.
was_cache_hit_again = schema.resolve_persisted_query(sha256_hash)
# True
```

`resolve_persisted_query` returns `False` when `query` was freshly parsed,
validated, and registered under `sha256_hash`; `True` when `sha256_hash`
was already cached. It raises `bramble.GraphQLError` with:

- `code=PERSISTED_QUERY_NOT_FOUND` on a hash-only miss -- the client should
  resend the request with the full `query` text included. The error
  message is the exact string `"PersistedQueryNotFound"`, matching what
  Apollo Client's own APQ link matches on to trigger its automatic retry.
- `code=PERSISTED_QUERY_MISMATCH` if a provided `query`'s hash doesn't
  actually match the given `sha256_hash`.

## Over HTTP

Every [HTTP integration](../integrations/index.md) speaks APQ out of the box --
there is nothing to enable. A request carrying
`extensions.persistedQuery.sha256Hash` is resolved against the schema's cache
before execution, and may omit `query` entirely:

```jsonc
// First attempt: hash only.
{"extensions": {"persistedQuery": {"version": 1, "sha256Hash": "abc123..."}}}
// -> 200 {"data": null, "errors": [{"message": "PersistedQueryNotFound", ...}]}

// Client retries with the query text, which registers it.
{"query": "query { greet }",
 "extensions": {"persistedQuery": {"version": 1, "sha256Hash": "abc123..."}}}
// -> 200 {"data": {"greet": "..."}}

// Every later request can send the hash alone.
{"extensions": {"persistedQuery": {"version": 1, "sha256Hash": "abc123..."}}}
// -> 200 {"data": {"greet": "..."}}
```

A miss is returned as a **200 response with an error body**, not an HTTP
error. That is deliberate and required: Apollo Client's APQ link detects the
`PersistedQueryNotFound` message in the response body to trigger its automatic
retry, and never sees it if the request fails at the status level.

A request advertising a `persistedQuery` version other than `1` is treated as
an ordinary request, so an unknown future protocol version degrades rather
than failing.

## Executing a cached document directly

`resolve_persisted_query` only registers/checks the hash. To actually benefit
from the cache, use `prepare_persisted_query`, which returns the cached
document alongside the hit/miss flag, and hand that document to any of the
execution methods:

```python
prepared = schema.prepare_persisted_query(sha256_hash)
result = await schema.execute_async(None, document=prepared.document)
```

`document=` accepts the handle on `execute`, `execute_async`,
`execute_incremental`, and `subscribe_async`. When one is supplied the query
argument may be `None` -- a hash-only replay has no query text to pass.

The cache is per-`Schema` instance and holds the already parsed and validated
document, so executing it this way skips both parsing and validation. What is
*not* skipped is lowering: `@skip`/`@include` evaluation and argument
substitution depend on each request's own variable values, so those are redone
per request. That is why the same persisted query can be replayed with
different variables and produce different results.

Because a hash-only replay never carries the query text, `Info.query` is
`None` inside resolvers on that path -- bramble reports what the client
actually sent rather than reconstructing an approximation from the parsed
document.
