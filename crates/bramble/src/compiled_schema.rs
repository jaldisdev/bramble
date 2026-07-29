use std::collections::{HashMap, HashSet};

use bramble_core::persisted_query::PersistedQueryCache;
use bramble_core::schema::CompiledSchema;
use pyo3::prelude::*;

use crate::operation_directive_info::PyOperationDirectiveInfo;
use crate::schema_directive_info::PySchemaDirectiveInfo;
use crate::type_info::{PyTypeInfo, extract_applied_directives, validate_directive_locations};
use crate::union_info::PyUnionInfo;

/// Wraps the assembled `CompiledSchema` for the Python side to hold onto (as an opaque handle)
/// and hand back to `validate_query` -- not meant to be introspected field-by-field from Python;
/// `Schema()`'s own Python-level attributes (`types_by_name`, etc., from Task 8b) already cover
/// that for schema-shape debugging/testing.
#[pyclass(name = "CompiledSchema", frozen)]
pub struct PyCompiledSchema {
    pub schema: CompiledSchema,
}

/// Assembles a `CompiledSchema` from what `Schema()`'s Python-side graph walker (Task 8b) already
/// discovered -- the `__bramble_type_info__`/`__bramble_union_info__`/`__bramble_directive_info__`
/// objects for every reachable type/union/directive, plus the resolved scalar names. Each of those
/// `PyTypeInfo`/`PyUnionInfo`/`PyOperationDirectiveInfo` objects already carries the original Rust
/// `TypeDefinition`/`UnionDefinition`/`OperationDirectiveDefinition` it was built from (see their
/// `definition` fields), so this is just re-keying already-computed data by name, not re-deriving
/// anything from the Python classes themselves.
#[pyfunction]
#[pyo3(signature = (*, query_type_name, mutation_type_name=None, subscription_type_name=None, types, unions, directives, schema_directives, scalar_names, scalar_directives=Vec::new(), auto_camel_case=true))]
#[allow(clippy::too_many_arguments)]
pub fn compile_schema(
    query_type_name: String,
    mutation_type_name: Option<String>,
    subscription_type_name: Option<String>,
    types: Vec<PyRef<'_, PyTypeInfo>>,
    unions: Vec<PyRef<'_, PyUnionInfo>>,
    directives: Vec<PyRef<'_, PyOperationDirectiveInfo>>,
    schema_directives: Vec<PyRef<'_, PySchemaDirectiveInfo>>,
    scalar_names: Vec<String>,
    scalar_directives: Vec<(String, Bound<'_, PyAny>)>,
    auto_camel_case: bool,
) -> PyResult<PyCompiledSchema> {
    let types = types
        .iter()
        .map(|info| (info.definition.name.clone(), info.definition.clone()))
        .collect::<HashMap<_, _>>();

    let unions = unions
        .iter()
        .map(|info| (info.definition.name.clone(), info.definition.clone()))
        .collect::<HashMap<_, _>>();

    let operation_directives = directives
        .iter()
        .map(|info| (info.definition.name.clone(), info.definition.clone()))
        .collect::<HashMap<_, _>>();

    let schema_directives = schema_directives
        .iter()
        .map(|info| (info.definition.name.clone(), info.definition.clone()))
        .collect::<HashMap<_, _>>();

    // A scalar has no other Rust-side IR of its own (unlike a type/field) to validate/extract
    // directives on the way `_type.py` does for types/fields before `process_type` runs, so both
    // steps happen here instead, right where the scalar's own name is already known.
    let mut scalar_applied_directives = HashMap::with_capacity(scalar_directives.len());
    for (name, directives) in &scalar_directives {
        validate_directive_locations(directives, "SCALAR", name)?;
        scalar_applied_directives.insert(name.clone(), extract_applied_directives(directives)?);
    }

    let schema = CompiledSchema {
        types,
        unions,
        query_type_name,
        mutation_type_name,
        subscription_type_name,
        operation_directives,
        schema_directives,
        scalar_names: scalar_names.into_iter().collect::<HashSet<_>>(),
        scalar_applied_directives,
        auto_camel_case,
        persisted_query_cache: PersistedQueryCache::new(),
    };

    Ok(PyCompiledSchema { schema })
}
