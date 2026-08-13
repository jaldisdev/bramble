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

use std::collections::{HashMap, HashSet};

use bramble_core::persisted_query::PersistedQueryCache;
use bramble_core::schema::CompiledSchema;
use pyo3::prelude::*;

use crate::operation_directive_info::PyOperationDirectiveInfo;
use crate::schema_directive_info::PySchemaDirectiveInfo;
use crate::type_info::{PyTypeInfo, SchemaError, extract_applied_directives, validate_directive_locations};
use crate::union_info::PyUnionInfo;

/// Wraps the assembled `CompiledSchema` for the Python side to hold onto (as an opaque handle)
/// and hand back to `validate_query` -- not meant to be introspected field-by-field from Python;
/// `Schema()`'s own Python-level attributes (`types_by_name`, etc., from Task 8b) already cover
/// that for schema-shape debugging/testing.
#[pyclass(name = "CompiledSchema", frozen)]
pub struct PyCompiledSchema {
    pub schema: CompiledSchema,
}

#[pymethods]
impl PyCompiledSchema {
    /// Every scalar name this schema declares: the explicitly registered ones plus whichever
    /// standard-library built-ins it actually references, which nothing on the Python side knows
    /// about -- they are only ever added below, in `compile_schema`. Introspection reads this so it
    /// reports exactly the scalars the SDL declares; deriving the set separately in Python is what
    /// let `__schema.types` omit `DateTime` while the SDL declared it, which a client building a
    /// schema from an introspection result rejects as a dangling reference.
    ///
    /// Sorted because the underlying set's iteration order is not stable across runs.
    #[getter]
    fn scalar_names(&self) -> Vec<String> {
        let mut names = self.schema.scalar_names.iter().cloned().collect::<Vec<_>>();
        names.sort();
        names
    }

    #[getter]
    fn scalar_descriptions(&self) -> HashMap<String, String> {
        self.schema.scalar_descriptions.clone()
    }
}

/// Assembles a `CompiledSchema` from what `Schema()`'s Python-side graph walker (Task 8b) already
/// discovered -- the `__bramble_type_info__`/`__bramble_union_info__`/`__bramble_directive_info__`
/// objects for every reachable type/union/directive, plus the resolved scalar names. Each of those
/// `PyTypeInfo`/`PyUnionInfo`/`PyOperationDirectiveInfo` objects already carries the original Rust
/// `TypeDefinition`/`UnionDefinition`/`OperationDirectiveDefinition` it was built from (see their
/// `definition` fields), so this is just re-keying already-computed data by name, not re-deriving
/// anything from the Python classes themselves.
#[pyfunction]
#[pyo3(signature = (*, query_type_name, mutation_type_name=None, subscription_type_name=None, types, unions, directives, schema_directives, scalar_names, scalar_directives=Vec::new(), scalar_descriptions=Vec::new(), scalar_specified_by_urls=Vec::new(), auto_camel_case=true, schema_applied_directives=None))]
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
    scalar_descriptions: Vec<(String, Option<String>)>,
    scalar_specified_by_urls: Vec<(String, Option<String>)>,
    auto_camel_case: bool,
    schema_applied_directives: Option<Bound<'_, PyAny>>,
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

    let scalar_descriptions = scalar_descriptions
        .into_iter()
        .filter_map(|(name, description)| description.map(|description| (name, description)))
        .collect::<HashMap<_, _>>();

    let scalar_specified_by_urls = scalar_specified_by_urls
        .into_iter()
        .filter_map(|(name, url)| url.map(|url| (name, url)))
        .collect::<HashMap<_, _>>();

    let schema_applied_directives = match &schema_applied_directives {
        Some(directives) => {
            validate_directive_locations(directives, "SCHEMA", "schema")?;
            extract_applied_directives(directives)?
        }
        None => Vec::new(),
    };

    let mut scalar_names: HashSet<String> = scalar_names.into_iter().collect();
    let mut scalar_descriptions = scalar_descriptions;

    // Declare the standard-library scalars this schema actually refers to. bramble names and
    // serialises `datetime`/`date`/`time`/`Decimal`/`UUID` with no registration, so without this
    // the SDL says `when: DateTime!` while defining no `DateTime` -- output a spec-compliant parser
    // rejects outright, and which disagrees with what introspection reports. Registering one
    // explicitly still wins: an existing entry keeps its own directives and description.
    let referenced = bramble_core::schema::referenced_type_names(&types);
    for (name, description) in bramble_core::schema::BUILTIN_SCALARS {
        if !referenced.contains(*name) || types.contains_key(*name) || unions.contains_key(*name) {
            continue;
        }
        if scalar_names.insert((*name).to_string()) {
            scalar_descriptions.insert((*name).to_string(), (*description).to_string());
        }
    }

    bramble_core::schema::validate_schema_shape(&types, &unions, &scalar_names).map_err(SchemaError::new_err)?;

    let schema = CompiledSchema {
        types,
        unions,
        query_type_name,
        mutation_type_name,
        subscription_type_name,
        operation_directives,
        schema_directives,
        schema_applied_directives,
        scalar_names,
        scalar_applied_directives,
        scalar_descriptions,
        scalar_specified_by_urls,
        auto_camel_case,
        persisted_query_cache: PersistedQueryCache::new(),
    };

    Ok(PyCompiledSchema { schema })
}
