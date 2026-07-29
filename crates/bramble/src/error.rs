use bramble_core::error::GraphQLError as CoreGraphQLError;
use pyo3::create_exception;
use pyo3::exceptions::PyException;
use pyo3::prelude::*;
use pyo3::types::PyDict;

create_exception!(
    _bramble,
    GraphQLError,
    PyException,
    "Base exception for spec-shaped GraphQL errors (message/locations/path/extensions). \
     bramble._error.GraphQLError subclasses this in Python with the structured fields; this \
     Rust-defined base is what's shared with errors raised natively during parsing/validation."
);

/// Converts a Rust-native `GraphQLError` into a raised `bramble._error.GraphQLError` (the Python
/// subclass that carries `code`/`locations`/`path`/`extensions`, not just this module's bare
/// exception base), so errors raised natively during parsing/pruning look identical to ones
/// raised from Python. Reuses the `ErrorCode` enum's own serde rendering (`SCREAMING_SNAKE_CASE`)
/// rather than hand-duplicating a second name-to-string mapping.
pub fn raise(py: Python<'_>, error: Box<CoreGraphQLError>) -> PyErr {
    match build_python_error(py, &error) {
        Ok(instance) => PyErr::from_value(instance),
        Err(construction_error) => construction_error,
    }
}

fn build_python_error<'py>(py: Python<'py>, error: &CoreGraphQLError) -> PyResult<Bound<'py, PyAny>> {
    let error_module = py.import("bramble._error")?;
    let error_class = error_module.getattr("GraphQLError")?;
    let error_code_class = error_module.getattr("ErrorCode")?;

    let code_name = serde_json::to_value(error.extensions.code)
        .ok()
        .and_then(|value| value.as_str().map(str::to_string))
        .unwrap_or_default();
    let code_value = error_code_class.getattr(code_name.as_str())?;

    let kwargs = PyDict::new(py);
    kwargs.set_item("code", code_value)?;
    error_class.call((error.message.clone(),), Some(&kwargs))
}
