use bramble_core::schema::{DirectiveFieldDefinition, SchemaDirectiveDefinition, SchemaDirectiveLocation};
use pyo3::prelude::*;
use pyo3::types::PyType;

use crate::type_info::SchemaError;

#[pyclass(name = "DirectiveFieldInfo", frozen, get_all, skip_from_py_object)]
#[derive(Clone)]
pub struct PyDirectiveFieldInfo {
    pub name: String,
    pub graphql_name: Option<String>,
    pub type_repr: Option<String>,
}

impl From<DirectiveFieldDefinition> for PyDirectiveFieldInfo {
    fn from(field: DirectiveFieldDefinition) -> Self {
        Self {
            name: field.name,
            graphql_name: field.graphql_name,
            type_repr: field.type_repr,
        }
    }
}

#[pyclass(name = "SchemaDirectiveInfo", frozen, get_all, skip_from_py_object)]
pub struct PySchemaDirectiveInfo {
    pub name: String,
    pub description: Option<String>,
    pub locations: Vec<String>,
    pub fields: Vec<PyDirectiveFieldInfo>,
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
#[pyo3(signature = (cls, *, locations, name=None, description=None))]
pub fn describe_schema_directive(
    py: Python<'_>,
    cls: &Bound<'_, PyType>,
    locations: Vec<String>,
    name: Option<String>,
    description: Option<String>,
) -> PyResult<PySchemaDirectiveInfo> {
    let parsed_locations = locations
        .iter()
        .map(|value| parse_location(value))
        .collect::<PyResult<Vec<_>>>()?;

    let dataclass_fields = py.import("dataclasses")?.call_method1("fields", (cls,))?;
    let fields = dataclass_fields
        .try_iter()?
        .map(|dataclass_field| {
            let dataclass_field = dataclass_field?;
            let name: String = dataclass_field.getattr("name")?.extract()?;
            let type_repr = dataclass_field
                .getattr("type")?
                .str()
                .ok()
                .and_then(|s| s.extract::<String>().ok());
            let graphql_name: Option<String> = dataclass_field
                .getattr("graphql_name")
                .ok()
                .and_then(|value| value.extract().ok());

            Ok(DirectiveFieldDefinition {
                name,
                graphql_name,
                type_repr,
            })
        })
        .collect::<PyResult<Vec<_>>>()?;

    let resolved_name = match name {
        Some(name) => name,
        None => class_name_to_directive_name(&cls.getattr("__name__")?.extract::<String>()?),
    };

    let definition = SchemaDirectiveDefinition {
        name: resolved_name,
        description,
        locations: parsed_locations,
        fields,
    };

    Ok(PySchemaDirectiveInfo {
        name: definition.name,
        description: definition.description,
        locations: definition
            .locations
            .into_iter()
            .map(|location| location_str(location).to_string())
            .collect(),
        fields: definition.fields.into_iter().map(PyDirectiveFieldInfo::from).collect(),
    })
}
