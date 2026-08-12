//
// This source file is part of the Bramble open source project.
//
// Copyright (c) 2026 Jaldis B.V.
//
// Licensed under the MIT OR Apache-2.0 license (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     https://opensource.org/licenses/MIT
//     https://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.
//

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

#[cfg(test)]
mod tests {
    use std::collections::{HashMap, HashSet};

    use super::*;
    use crate::schema::{CompiledSchema, FieldDefinition, GraphQLType, TypeDefinition, TypeKind};

    fn schema() -> CompiledSchema {
        let mut types = HashMap::new();
        types.insert(
            "Query".to_string(),
            TypeDefinition {
                kind: TypeKind::Type,
                name: "Query".to_string(),
                description: None,
                one_of: false,
                interfaces: Vec::new(),
                enum_values: Vec::new(),
                applied_directives: Vec::new(),
                fields: vec![FieldDefinition {
                    name: "greet".to_string(),
                    graphql_name: None,
                    graphql_type: GraphQLType::NonNull(Box::new(GraphQLType::Named("String".to_string()))),
                    description: None,
                    has_resolver: false,
                    parent_parameter: None,
                    info_parameter: None,
                    arguments: Vec::new(),
                    applied_directives: Vec::new(),
                }],
            },
        );
        CompiledSchema {
            types,
            unions: HashMap::new(),
            query_type_name: "Query".to_string(),
            mutation_type_name: None,
            subscription_type_name: None,
            operation_directives: HashMap::new(),
            schema_directives: HashMap::new(),
            schema_applied_directives: Vec::new(),
            scalar_names: HashSet::new(),
            scalar_applied_directives: HashMap::new(),
            scalar_descriptions: HashMap::new(),
            auto_camel_case: true,
            persisted_query_cache: PersistedQueryCache::new(),
        }
    }

    const QUERY: &str = "query { greet }";

    #[test]
    fn sha256_hex_matches_the_known_digest_clients_compute() {
        // Lowercase hex of the raw string, no normalization -- the client computes the same digest
        // independently, so any drift here silently breaks every APQ client at once.
        assert_eq!(
            sha256_hex("abc"),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        );
        assert_eq!(sha256_hex("").len(), 64);
    }

    #[test]
    fn a_hash_only_miss_reports_the_exact_apollo_message() {
        // Apollo Client's APQ link matches this literal string to trigger its resend-with-query
        // retry. Paraphrasing it breaks interop with every such client.
        let error = resolve_persisted_query(&schema(), &sha256_hex(QUERY), None, None)
            .expect_err("a hash-only miss must error");
        assert_eq!(error.message, "PersistedQueryNotFound");
        assert_eq!(error.extensions.code, ErrorCode::PersistedQueryNotFound);
    }

    #[test]
    fn a_query_plus_hash_registers_and_then_hits() {
        let schema = schema();
        let hash = sha256_hex(QUERY);

        assert_eq!(
            resolve_persisted_query(&schema, &hash, Some(QUERY), None).unwrap(),
            PersistedQueryOutcome::Registered
        );
        assert_eq!(
            resolve_persisted_query(&schema, &hash, None, None).unwrap(),
            PersistedQueryOutcome::CacheHit
        );
    }

    #[test]
    fn a_mismatched_hash_is_rejected_before_anything_is_cached() {
        let schema = schema();
        let wrong_hash = "0".repeat(64);

        let error = resolve_persisted_query(&schema, &wrong_hash, Some(QUERY), None)
            .expect_err("a mismatched hash must error");
        assert_eq!(error.extensions.code, ErrorCode::PersistedQueryMismatch);
        // The content-addressing guarantee: nothing was stored under the claimed hash.
        assert!(schema.persisted_query_cache.get(&wrong_hash).is_none());
    }

    #[test]
    fn an_invalid_query_is_not_cached_even_though_its_hash_matched() {
        let schema = schema();
        let bad = "query { doesNotExist }";
        let hash = sha256_hex(bad);

        assert!(resolve_persisted_query(&schema, &hash, Some(bad), None).is_err());
        assert!(
            schema.persisted_query_cache.get(&hash).is_none(),
            "a document that failed validation must never become replayable by hash"
        );
    }

    #[test]
    fn a_malformed_query_is_not_cached_either() {
        let schema = schema();
        let bad = "query { greet";
        let hash = sha256_hex(bad);

        assert!(resolve_persisted_query(&schema, &hash, Some(bad), None).is_err());
        assert!(schema.persisted_query_cache.get(&hash).is_none());
    }

    #[test]
    fn each_schema_gets_its_own_cache() {
        let first = schema();
        let hash = sha256_hex(QUERY);
        resolve_persisted_query(&first, &hash, Some(QUERY), None).unwrap();

        let second = schema();
        assert!(
            resolve_persisted_query(&second, &hash, None, None).is_err(),
            "a fresh schema must start with an empty cache"
        );
    }

    #[test]
    fn flush_empties_the_cache() {
        let schema = schema();
        let hash = sha256_hex(QUERY);
        resolve_persisted_query(&schema, &hash, Some(QUERY), None).unwrap();

        schema.persisted_query_cache.flush();

        assert!(resolve_persisted_query(&schema, &hash, None, None).is_err());
    }

    #[test]
    fn the_cache_evicts_rather_than_growing_without_bound() {
        // The 1000-entry `moka` bound was never exercised by any test. An unbounded cache keyed by
        // client-supplied hashes would be a memory-exhaustion vector, so this asserts the bound is
        // real -- not *which* entries survive, which is moka's own eviction policy to decide.
        let cache = PersistedQueryCache::new();
        let document = crate::parse_document(QUERY).unwrap();

        for index in 0..5_000 {
            cache.insert(format!("hash-{index}"), document.clone());
        }
        // moka evicts asynchronously; force the pending work through so the bound is
        // observable synchronously. Reaching into the private field is fine here -- a child module
        // can see it, and this is not behaviour worth exposing on the public wrapper.
        cache.cache.run_pending_tasks();

        let surviving = (0..5_000).filter(|index| cache.get(&format!("hash-{index}")).is_some()).count();
        assert!(surviving > 0, "the cache should still hold recent entries");
        assert!(surviving <= 1000, "the cache must stay bounded, but held {surviving} entries");
    }
}
