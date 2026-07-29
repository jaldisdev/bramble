use pyo3::prelude::*;

use crate::compiled_schema::PyCompiledSchema;
use crate::error::raise;

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
