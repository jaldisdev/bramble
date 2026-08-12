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

use pyo3::prelude::*;

use crate::compiled_schema::PyCompiledSchema;
use crate::error::raise;
use crate::persisted_query::PyParsedDocument;

/// Validates an already-parsed document against `schema`. The counterpart to `parse_query`: taking
/// the handle rather than the source is what keeps parsing and validation separate steps, so
/// `SchemaExtension.on_parse`/`on_validate` can wrap one each.
#[pyfunction]
#[pyo3(signature = (document, schema, operation_name=None))]
pub fn validate_document(
    py: Python<'_>,
    document: &PyParsedDocument,
    schema: &PyCompiledSchema,
    operation_name: Option<String>,
) -> PyResult<()> {
    bramble_core::validation::validate_query(&document.document, &schema.schema, operation_name.as_deref())
        .map_err(|error| raise(py, error))
}

/// Parses `query` and validates its (optionally named) operation against `schema` per §7a:
/// requested fields exist on their parent type, arguments are declared and type-check, directives
/// are used at legal locations, and fragment spreads/inline fragments target real types. Raises
/// the first violation found as a `bramble.GraphQLError` (matching the parser's own
/// single-error-per-call behavior, per §2/§8's accepted tradeoff); returns `None` if the query is
/// valid.
#[pyfunction]
#[pyo3(signature = (query, schema, operation_name=None))]
pub fn validate_query(py: Python<'_>, query: &str, schema: &PyCompiledSchema, operation_name: Option<String>) -> PyResult<()> {
    let document = bramble_core::parse_document(query).map_err(|error| raise(py, error))?;
    bramble_core::validation::validate_query(&document, &schema.schema, operation_name.as_deref())
        .map_err(|error| raise(py, error))
}
