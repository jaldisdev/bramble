use bramble_core::schema::{OperationDirectiveDefinition, OperationDirectiveLocation};
use pyo3::prelude::*;

use crate::resolver_binding::{classify_argument, resolve_annotations};
use crate::type_info::{PyArgumentInfo, SchemaError};

#[pyclass(name = "OperationDirectiveInfo", frozen, get_all, skip_from_py_object)]
pub struct PyOperationDirectiveInfo {
    pub name: String,
    pub description: Option<String>,
    pub locations: Vec<String>,
    pub value_parameter: Option<String>,
    pub arguments: Vec<PyArgumentInfo>,
}

fn parse_location(value: &str) -> PyResult<OperationDirectiveLocation> {
    match value {
        "QUERY" => Ok(OperationDirectiveLocation::Query),
        "MUTATION" => Ok(OperationDirectiveLocation::Mutation),
        "SUBSCRIPTION" => Ok(OperationDirectiveLocation::Subscription),
        "FIELD" => Ok(OperationDirectiveLocation::Field),
        "FRAGMENT_DEFINITION" => Ok(OperationDirectiveLocation::FragmentDefinition),
        "FRAGMENT_SPREAD" => Ok(OperationDirectiveLocation::FragmentSpread),
        "INLINE_FRAGMENT" => Ok(OperationDirectiveLocation::InlineFragment),
        other => Err(SchemaError::new_err(format!(
            "unknown operation directive location '{other}'"
        ))),
    }
}

fn location_str(location: OperationDirectiveLocation) -> String {
    serde_json::to_value(location)
        .ok()
        .and_then(|value| value.as_str().map(str::to_string))
        .unwrap_or_default()
}

/// The spec doesn't explicitly restate the "camelCase default name" rule for operation
/// directives (only Task 7's schema directives), but GraphQL's own built-ins (`@skip`,
/// `@include`) and the ecosystem's convention are consistently camelCase, and Python directive
/// functions are named snake_case (unlike schema directives' PascalCase classes) -- so this
/// converts words, not just the first letter (`turn_uppercase` -> `turnUppercase`).
fn snake_to_camel_case(name: &str) -> String {
    let mut result = String::with_capacity(name.len());
    let mut capitalize_next = false;
    for ch in name.chars() {
        if ch == '_' {
            capitalize_next = true;
        } else if capitalize_next {
            result.extend(ch.to_uppercase());
            capitalize_next = false;
        } else {
            result.push(ch);
        }
    }
    result
}

/// Classifies a custom operation directive function's parameters per §7: `DirectiveValue[T]` ->
/// the field's already-resolved value, anything else -> one of the directive's own arguments at
/// its use site in the query (reusing Task 4's exact argument-binding rules via
/// `classify_argument`). Unlike resolver binding, there's no enclosing class to seed
/// `typing.get_type_hints`'s `localns` with -- a directive is always a standalone function.
#[pyfunction]
#[pyo3(signature = (func, *, locations, name=None, description=None))]
pub fn describe_operation_directive(
    py: Python<'_>,
    func: &Bound<'_, PyAny>,
    locations: Vec<String>,
    name: Option<String>,
    description: Option<String>,
) -> PyResult<PyOperationDirectiveInfo> {
    let parsed_locations = locations
        .iter()
        .map(|value| parse_location(value))
        .collect::<PyResult<Vec<_>>>()?;

    let inspect = py.import("inspect")?;
    let typing = py.import("typing")?;
    let directive_value_class = py.import("bramble.directive")?.getattr("DirectiveValue")?;
    let empty = inspect.getattr("Parameter")?.getattr("empty")?;

    let signature = inspect.call_method1("signature", (func,))?;
    let parameters = signature.getattr("parameters")?.call_method0("values")?;
    let resolved_hints = resolve_annotations(py, &typing, None, func)?;

    let mut value_parameter: Option<String> = None;
    let mut arguments = Vec::new();

    for parameter in parameters.try_iter()? {
        let parameter = parameter?;
        let parameter_name: String = parameter.getattr("name")?.extract()?;
        let raw_annotation = parameter.getattr("annotation")?;

        if raw_annotation.is(&empty) {
            return Err(SchemaError::new_err(format!(
                "directive parameter '{parameter_name}' has no type annotation; annotate it as \
                 DirectiveValue[T] or a concrete argument type"
            )));
        }

        let annotation = resolved_hints.get_item(&parameter_name)?.unwrap_or(raw_annotation);
        let origin = typing.call_method1("get_origin", (&annotation,))?;

        if annotation.is(&directive_value_class) || origin.is(&directive_value_class) {
            if value_parameter.is_some() {
                return Err(SchemaError::new_err(
                    "directive declares more than one DirectiveValue[T] parameter",
                ));
            }
            value_parameter = Some(parameter_name);
            continue;
        }

        let default = parameter.getattr("default")?;
        let has_default = !default.is(&empty);
        arguments.push(classify_argument(py, &typing, parameter_name, annotation, has_default)?);
    }

    let resolved_name = match name {
        Some(name) => name,
        None => snake_to_camel_case(&func.getattr("__name__")?.extract::<String>()?),
    };

    let definition = OperationDirectiveDefinition {
        name: resolved_name,
        description,
        locations: parsed_locations,
        value_parameter,
        arguments,
    };

    Ok(PyOperationDirectiveInfo {
        name: definition.name,
        description: definition.description,
        locations: definition.locations.into_iter().map(location_str).collect(),
        value_parameter: definition.value_parameter,
        arguments: definition.arguments.into_iter().map(PyArgumentInfo::from).collect(),
    })
}
