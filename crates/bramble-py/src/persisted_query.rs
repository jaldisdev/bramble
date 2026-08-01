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

use bramble_core::persisted_query::{PersistedQueryOutcome, resolve_persisted_query as core_resolve_persisted_query};
use pyo3::prelude::*;

use crate::compiled_schema::PyCompiledSchema;
use crate::error::raise;

/// Implements the Automatic Persisted Queries protocol (§10) against `schema`'s cache. Returns
/// `True` if `sha256_hash` was already cached (a hash-only request that hit), `False` if the
/// query was freshly parsed/validated and just registered under its hash. Raises
/// `bramble.GraphQLError` with `code=PERSISTED_QUERY_NOT_FOUND` on a hash-only miss (per the
/// protocol, the caller should resend with `query` included) or `code=PERSISTED_QUERY_MISMATCH`
/// if a provided `query`'s hash doesn't match `sha256_hash`.
#[pyfunction]
#[pyo3(signature = (sha256_hash, schema, *, query=None, operation_name=None))]
pub fn resolve_persisted_query(
    py: Python<'_>,
    sha256_hash: &str,
    schema: &PyCompiledSchema,
    query: Option<&str>,
    operation_name: Option<String>,
) -> PyResult<bool> {
    let outcome = core_resolve_persisted_query(&schema.schema, sha256_hash, query, operation_name.as_deref())
        .map_err(|error| raise(py, error))?;
    Ok(outcome == PersistedQueryOutcome::CacheHit)
}
