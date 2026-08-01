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

use bramble_core::schema::{DirectiveFieldDefinition, SchemaDirectiveDefinition, SchemaDirectiveLocation};
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyType};

use crate::type_info::SchemaError;
use crate::typing_utils::{resolve_graphql_type, seed_lazy_namespace_for_class};

#[pyclass(name = "DirectiveFieldInfo", frozen, get_all, skip_from_py_object)]
#[derive(Clone)]
pub struct PyDirectiveFieldInfo {
    pub name: String,
    pub graphql_name: Option<String>,
    pub graphql_type: String,
}

impl From<DirectiveFieldDefinition> for PyDirectiveFieldInfo {
    fn from(field: DirectiveFieldDefinition) -> Self {
        Self {
            name: field.name,
            graphql_name: field.graphql_name,
            graphql_type: field.graphql_type.to_sdl_string(),
        }
    }
}

#[pyclass(name = "SchemaDirectiveInfo", frozen, skip_from_py_object)]
pub struct PySchemaDirectiveInfo {
    #[pyo3(get)]
    pub name: String,
    #[pyo3(get)]
    pub description: Option<String>,
    #[pyo3(get)]
    pub locations: Vec<String>,
    #[pyo3(get)]
    pub fields: Vec<PyDirectiveFieldInfo>,
    #[pyo3(get)]
    pub repeatable: bool,
    /// Not Python-exposed -- see `PyTypeInfo::definition`'s doc comment for why. Lets
    /// `compile_schema` (§9/§12) re-key an already-computed `SchemaDirectiveInfo` by name for SDL
    /// rendering, the same way it already does for types/unions/operation directives.
    pub definition: SchemaDirectiveDefinition,
}

fn parse_location(value: &str) -> PyResult<SchemaDirectiveLocation> {
    match value {
        "SCHEMA" => Ok(SchemaDirectiveLocation::Schema),
        "SCALAR" => Ok(SchemaDirectiveLocation::Scalar),
        "OBJECT" => Ok(SchemaDirectiveLocation::Object),
        "FIELD_DEFINITION" => Ok(SchemaDirectiveLocation::FieldDefinition),
        "ARGUMENT_DEFINITION" => Ok(SchemaDirectiveLocation::ArgumentDefinition),
        "INTERFACE" => Ok(SchemaDirectiveLocation::Interface),
        "UNION" => Ok(SchemaDirectiveLocation::Union),
        "ENUM" => Ok(SchemaDirectiveLocation::Enum),
        "ENUM_VALUE" => Ok(SchemaDirectiveLocation::EnumValue),
        "INPUT_OBJECT" => Ok(SchemaDirectiveLocation::InputObject),
        "INPUT_FIELD_DEFINITION" => Ok(SchemaDirectiveLocation::InputFieldDefinition),
        other => Err(SchemaError::new_err(format!(
            "unknown schema directive location '{other}'"
        ))),
    }
}

fn location_str(location: SchemaDirectiveLocation) -> &'static str {
    match location {
        SchemaDirectiveLocation::Schema => "SCHEMA",
        SchemaDirectiveLocation::Scalar => "SCALAR",
        SchemaDirectiveLocation::Object => "OBJECT",
        SchemaDirectiveLocation::FieldDefinition => "FIELD_DEFINITION",
        SchemaDirectiveLocation::ArgumentDefinition => "ARGUMENT_DEFINITION",
        SchemaDirectiveLocation::Interface => "INTERFACE",
        SchemaDirectiveLocation::Union => "UNION",
        SchemaDirectiveLocation::Enum => "ENUM",
        SchemaDirectiveLocation::EnumValue => "ENUM_VALUE",
        SchemaDirectiveLocation::InputObject => "INPUT_OBJECT",
        SchemaDirectiveLocation::InputFieldDefinition => "INPUT_FIELD_DEFINITION",
    }
}

/// The spec's "directive names default to camelCase" default: decapitalize the class's own
/// PascalCase name (`Keys` -> `keys`), matching the conventional GraphQL directive-naming style.
/// Distinct from resolving a *field's* name -- fields keep their Python attribute name as-is
/// unless overridden via `directive_field(name=...)`; only the directive's own name gets this
/// default treatment.
fn class_name_to_directive_name(class_name: &str) -> String {
    let mut chars = class_name.chars();
    match chars.next() {
        Some(first) => first.to_lowercase().chain(chars).collect(),
        None => String::new(),
    }
}

#[pyfunction]
#[pyo3(signature = (cls, *, locations, name=None, description=None, repeatable=false))]
pub fn describe_schema_directive(
    py: Python<'_>,
    cls: &Bound<'_, PyType>,
    locations: Vec<String>,
    name: Option<String>,
    description: Option<String>,
    repeatable: bool,
) -> PyResult<PySchemaDirectiveInfo> {
    let parsed_locations = locations
        .iter()
        .map(|value| parse_location(value))
        .collect::<PyResult<Vec<_>>>()?;

    let typing = py.import("typing")?;
    let cls_name: String = cls.getattr("__name__")?.extract()?;
    let localns = PyDict::new(py);
    localns.set_item(&cls_name, cls)?;
    seed_lazy_namespace_for_class(py, cls.as_any(), &localns)?;
    let kwargs = PyDict::new(py);
    kwargs.set_item("localns", &localns)?;
    kwargs.set_item("include_extras", true)?;
    let resolved_hints = typing
        .call_method("get_type_hints", (cls,), Some(&kwargs))
        .ok()
        .and_then(|hints| hints.cast::<PyDict>().ok().cloned())
        .unwrap_or_else(|| PyDict::new(py));

    let dataclass_fields = py.import("dataclasses")?.call_method1("fields", (cls,))?;
    let fields = dataclass_fields
        .try_iter()?
        .map(|dataclass_field| {
            let dataclass_field = dataclass_field?;
            let name: String = dataclass_field.getattr("name")?.extract()?;
            let raw_type = dataclass_field.getattr("type")?;
            let resolved_type = resolved_hints.get_item(&name)?.unwrap_or(raw_type);
            let graphql_type = resolve_graphql_type(py, &typing, &resolved_type)?;
            let graphql_name: Option<String> = dataclass_field
                .getattr("graphql_name")
                .ok()
                .and_then(|value| value.extract().ok());

            Ok(DirectiveFieldDefinition {
                name,
                graphql_name,
                graphql_type,
            })
        })
        .collect::<PyResult<Vec<_>>>()?;

    let resolved_name = match name {
        Some(name) => name,
        None => class_name_to_directive_name(&cls_name),
    };

    let definition = SchemaDirectiveDefinition {
        name: resolved_name,
        description,
        locations: parsed_locations,
        fields,
        repeatable,
    };

    Ok(PySchemaDirectiveInfo {
        name: definition.name.clone(),
        description: definition.description.clone(),
        locations: definition
            .locations
            .iter()
            .copied()
            .map(|location| location_str(location).to_string())
            .collect(),
        fields: definition.fields.iter().cloned().map(PyDirectiveFieldInfo::from).collect(),
        repeatable: definition.repeatable,
        definition,
    })
}
