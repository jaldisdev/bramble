use std::sync::Arc;

use async_graphql_parser::types::ExecutableDocument;
use sha2::{Digest, Sha256};

use crate::error::{ErrorCode, GraphQLError, GraphQLResult};
use crate::schema::CompiledSchema;
use crate::validation::validate_query;

/// Whether a persisted-query request was served straight from the cache, or freshly
/// parsed/validated and just registered under its hash. Task 11's execution bridge doesn't exist
/// yet to actually *run* either outcome; this exists so behavior is externally observable/testable
/// now (a cache hit vs. a fresh parse+validate) without needing to expose `ExecutableDocument`
/// itself across the PyO3 boundary.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PersistedQueryOutcome {
    CacheHit,
    Registered,
}

/// An in-process, per-schema cache of parsed + validated query plans, keyed by the SHA-256 hex
/// digest of the raw query document text (the Apollo Automatic Persisted Queries convention --
/// hashing the raw string as-is, no normalization, since the client computes the same hash
/// independently). The cached value is the plan as it stands after Tasks 2/9 (parsed,
/// schema-validated) -- deliberately *not* further "lowered" against per-request variable values,
/// since `@skip`/`@include` and custom operation-directive transforms both depend on those (§7's
/// caveat); the cache represents how to execute the query shape, not a resolved request.
#[derive(Clone)]
pub struct PersistedQueryCache {
    cache: moka::sync::Cache<String, Arc<ExecutableDocument>>,
}

impl PersistedQueryCache {
    #[must_use]
    pub fn new() -> Self {
        Self {
            cache: moka::sync::Cache::new(1000),
        }
    }

    #[must_use]
    pub fn get(&self, sha256_hash: &str) -> Option<Arc<ExecutableDocument>> {
        self.cache.get(sha256_hash)
    }

    pub fn insert(&self, sha256_hash: String, document: ExecutableDocument) {
        self.cache.insert(sha256_hash, Arc::new(document));
    }

    /// The natural point to flush is constructing a new `Schema()` (a new `CompiledSchema` means
    /// a new, empty `PersistedQueryCache` in the first place) -- this exists for an explicit hot
    /// reload that reuses an existing `CompiledSchema` instance, if that's ever supported.
    pub fn flush(&self) {
        self.cache.invalidate_all();
    }
}

impl Default for PersistedQueryCache {
    fn default() -> Self {
        Self::new()
    }
}

#[must_use]
pub fn sha256_hex(text: &str) -> String {
    let digest = Sha256::digest(text.as_bytes());
    digest.iter().map(|byte| format!("{byte:02x}")).collect()
}

/// Implements the Apollo Automatic Persisted Queries protocol (§10) against `schema`'s cache:
/// a hash-only request (`query: None`) is served from the cache or rejected with the standard
/// `PersistedQueryNotFound` error (message text matches Apollo's exact convention -- Apollo
/// Client's own APQ link checks for that literal string to trigger its resend-with-full-query
/// retry, so client interop depends on it being exact, not paraphrased). A hash-plus-query
/// request is verified against the claimed hash (guards the cache's content-addressing guarantee
/// against a mismatched/corrupted hash), then parsed, validated, and cached.
pub fn resolve_persisted_query(
    schema: &CompiledSchema,
    sha256_hash: &str,
    query: Option<&str>,
    operation_name: Option<&str>,
) -> GraphQLResult<PersistedQueryOutcome> {
    match query {
        None => {
            if schema.persisted_query_cache.get(sha256_hash).is_some() {
                Ok(PersistedQueryOutcome::CacheHit)
            } else {
                Err(Box::new(GraphQLError::new("PersistedQueryNotFound", ErrorCode::PersistedQueryNotFound)))
            }
        }
        Some(query_text) => {
            let computed_hash = sha256_hex(query_text);
            if computed_hash != sha256_hash {
                return Err(Box::new(GraphQLError::new(
                    "provided sha256Hash does not match the hash of the query",
                    ErrorCode::PersistedQueryMismatch,
                )));
            }

            let document = crate::parse_document(query_text)?;
            validate_query(&document, schema, operation_name)?;

            schema.persisted_query_cache.insert(sha256_hash.to_string(), document);
            Ok(PersistedQueryOutcome::Registered)
        }
    }
}
