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

This method only registers/checks the hash -- it doesn't execute the query
itself. A typical HTTP handler calls it first (using the hash and/or query
text from the request's `extensions.persistedQuery`/`query` fields), then
proceeds to `execute_async` with the now-known query text as usual.

The cache is per-`Schema` instance and caches the already parsed and
validated document, not just the raw query string -- so a cache hit skips
re-parsing/re-validating on every subsequent request, only re-binding
variables at execution time.
