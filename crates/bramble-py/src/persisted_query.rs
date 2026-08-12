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
use bramble_core::persisted_query::{PersistedQueryOutcome, resolve_persisted_query as core_resolve_persisted_query};
use pyo3::prelude::*;

use crate::compiled_schema::PyCompiledSchema;
use crate::error::raise;

/// Parses `query` into a reusable handle. The first step of the request pipeline: everything
/// downstream (`validate_document`, `lower_document`) takes the handle, so the text is parsed once.
#[pyfunction]
pub fn parse_query(py: Python<'_>, query: &str) -> PyResult<PyParsedDocument> {
    let document = bramble_core::parse_document(query).map_err(|error| raise(py, error))?;
    Ok(PyParsedDocument {
        document: Arc::new(document),
        query_text: query.to_string(),
    })
}

/// An opaque handle to a parsed GraphQL document.
///
/// Deliberately exposes no structure to Python: its only purpose is to be handed back to
/// `validate_document` and `lower_document`, so a document is parsed exactly once per request
/// rather than once per step. That separation is what lets `SchemaExtension.on_parse` and
/// `on_validate` wrap genuinely distinct steps instead of two calls that each re-parse.
///
/// Also what an APQ cache hit returns, which is what makes a hit cheaper than a cold request:
/// before this existed the cache stored the parsed document but nothing could retrieve it, so every
/// "hit" re-parsed and re-validated from the raw string.
///
/// `query_text` rides along because execution still needs the original source: `Info.query` exposes
/// it to resolvers, and error locations are only meaningful against it. It is empty for a hash-only
/// APQ replay, where the client genuinely never sent the text.
#[pyclass(name = "ParsedDocument", frozen)]
pub struct PyParsedDocument {
    pub document: Arc<ExecutableDocument>,
    #[pyo3(get)]
    pub query_text: String,
}

/// What `resolve_persisted_query` produced: whether the hash was already cached, plus the document
/// itself so the caller can execute it without going back through parse/validate.
#[pyclass(name = "PersistedQueryResult", frozen)]
pub struct PyPersistedQueryResult {
    /// `True` if `sha256_hash` was already in the cache; `False` if `query` was just parsed,
    /// validated, and registered under it.
    #[pyo3(get)]
    pub cache_hit: bool,
    #[pyo3(get)]
    pub document: Py<PyParsedDocument>,
}

/// Implements the Automatic Persisted Queries protocol (§10) against `schema`'s cache. Raises
/// `bramble.GraphQLError` with `code=PERSISTED_QUERY_NOT_FOUND` on a hash-only miss (per the
/// protocol, the caller should resend with `query` included) or `code=PERSISTED_QUERY_MISMATCH`
/// if a provided `query`'s hash doesn't match `sha256_hash`.
///
/// On success the cached document is returned alongside the hit/miss flag, so an APQ request can
/// skip straight to lowering rather than re-parsing text it has already parsed once.
#[pyfunction]
#[pyo3(signature = (sha256_hash, schema, *, query=None, operation_name=None))]
pub fn resolve_persisted_query(
    py: Python<'_>,
    sha256_hash: &str,
    schema: &PyCompiledSchema,
    query: Option<&str>,
    operation_name: Option<String>,
) -> PyResult<PyPersistedQueryResult> {
    let outcome = core_resolve_persisted_query(&schema.schema, sha256_hash, query, operation_name.as_deref())
        .map_err(|error| raise(py, error))?;

    // `resolve_persisted_query` has just guaranteed the entry exists (it either hit, or inserted
    // it) -- but read it back through the cache rather than assuming, since `moka` is free to evict
    // between the insert and this lookup under memory pressure. A miss here means re-parsing the
    // text we were handed, which is only reachable on the `Some(query)` path anyway.
    let document = match schema.schema.persisted_query_cache.get(sha256_hash) {
        Some(document) => document,
        None => Arc::new(bramble_core::parse_document(query.unwrap_or_default()).map_err(|error| raise(py, error))?),
    };

    // A cache hit doesn't carry the original text (only the parsed document is cached), so fall
    // back to whatever the request supplied. A hash-only hit has none, and `Info.query` is `None`
    // in that case -- honest about what is actually known, rather than reconstructing an
    // approximation of the source by printing the AST back out.
    let query_text = query.unwrap_or_default().to_string();

    Ok(PyPersistedQueryResult {
        cache_hit: outcome == PersistedQueryOutcome::CacheHit,
        document: Py::new(py, PyParsedDocument { document, query_text })?,
    })
}
