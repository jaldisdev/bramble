use std::collections::HashMap;

use bramble_core::skip_include::{PrunedField, prune_document};
use pyo3::prelude::*;
use pyo3::types::{PyBool, PyDict, PyList};
use serde_json::Value as JsonValue;

use crate::error::raise;

/// Converts a Python value into `serde_json::Value`, covering the subset actually needed for
/// GraphQL variable values (booleans, numbers, strings, null, lists, string-keyed objects).
/// `PyBool` must be checked before numeric extraction: Python's `bool` is a subclass of `int`, so
/// `value.extract::<i64>()` would otherwise silently accept `True`/`False` as `1`/`0`.
fn python_to_json_value(value: &Bound<'_, PyAny>) -> PyResult<JsonValue> {
    if value.is_none() {
        return Ok(JsonValue::Null);
    }
    if let Ok(boolean) = value.cast::<PyBool>() {
        return Ok(JsonValue::Bool(boolean.is_true()));
    }
    if let Ok(integer) = value.extract::<i64>() {
        return Ok(JsonValue::from(integer));
    }
    if let Ok(float) = value.extract::<f64>() {
        return Ok(JsonValue::from(float));
    }
    if let Ok(string) = value.extract::<String>() {
        return Ok(JsonValue::String(string));
    }
    if let Ok(list) = value.cast::<PyList>() {
        let items = list
            .iter()
            .map(|item| python_to_json_value(&item))
            .collect::<PyResult<Vec<_>>>()?;
        return Ok(JsonValue::Array(items));
    }
    if let Ok(dict) = value.cast::<PyDict>() {
        let mut map = serde_json::Map::new();
        for (key, item_value) in dict.iter() {
            let key: String = key.extract()?;
            map.insert(key, python_to_json_value(&item_value)?);
        }
        return Ok(JsonValue::Object(map));
    }
    Err(pyo3::exceptions::PyValueError::new_err(format!(
        "value of type '{}' is not JSON-serializable",
        value.get_type().name()?
    )))
}

#[pyclass(name = "PrunedField", frozen, get_all, skip_from_py_object)]
#[derive(Clone)]
pub struct PyPrunedField {
    pub name: String,
    pub selections: Vec<PyPrunedField>,
}

impl From<PrunedField> for PyPrunedField {
    fn from(field: PrunedField) -> Self {
        Self {
            name: field.name,
            selections: field.selections.into_iter().map(PyPrunedField::from).collect(),
        }
    }
}

/// Parses `query` and prunes `@skip`/`@include`d selections out of its (optionally named)
/// operation, evaluating each directive's `if` argument against `variable_values`. Fragment
/// spreads and inline fragments are flattened into their parent's field list. This is a
/// standalone slice of what will become the real lowering pass (Tasks 9-11) -- it re-parses the
/// query directly rather than accepting an already-lowered plan, since no such plan exists yet.
#[pyfunction]
#[pyo3(signature = (query, *, variable_values, operation_name=None))]
pub fn prune_selections(
    py: Python<'_>,
    query: &str,
    variable_values: &Bound<'_, PyDict>,
    operation_name: Option<String>,
) -> PyResult<Vec<PyPrunedField>> {
    let document = bramble_core::parse_document(query).map_err(|error| raise(py, error))?;

    let mut variables = HashMap::with_capacity(variable_values.len());
    for (key, value) in variable_values.iter() {
        let key: String = key.extract()?;
        variables.insert(key, python_to_json_value(&value)?);
    }

    let pruned = prune_document(&document, &variables, operation_name.as_deref())
        .map_err(|error| raise(py, error))?;

    Ok(pruned.into_iter().map(PyPrunedField::from).collect())
}
