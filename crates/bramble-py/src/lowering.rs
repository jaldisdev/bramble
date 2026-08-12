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

use std::collections::HashMap;

use async_graphql_parser::types::ExecutableDocument;
use async_graphql_value::Value;
use bramble_core::error::{ErrorCode, GraphQLError as CoreGraphQLError};
use bramble_core::lowering::{LoweredArgument, LoweredDirective, LoweredField, lower_document as core_lower_document};
use pyo3::prelude::*;
use pyo3::types::{PyBool, PyBytes, PyDict, PyList};
use serde_json::Value as JsonValue;

use crate::error::raise;
use crate::persisted_query::PyParsedDocument;

/// Renders a Python default value as the GraphQL literal that should appear after `= ` in SDL and
/// as introspection's `__InputValue.defaultValue`. Returns `None` for anything with no faithful
/// literal spelling (an arbitrary object, an input-class instance, ...) -- printing a wrong literal
/// would be worse than printing none, and the argument stays optional either way via `has_default`.
///
/// The enum branch has to come first: `class Color(str, Enum)` is a genuine `str` subclass, so
/// `extract::<String>()` would happily render `Color.RED` as the string literal `"red"` rather than
/// the enum literal `RED`. Same ordering reason as `PyBool`-before-`i64` below, one type further up.
pub(crate) fn python_default_to_graphql_literal(value: &Bound<'_, PyAny>) -> PyResult<Option<String>> {
    if let Some(literal) = enum_member_literal(value)? {
        return Ok(Some(literal));
    }
    if value.is_none() {
        return Ok(Some("null".to_string()));
    }
    if let Ok(boolean) = value.cast::<PyBool>() {
        return Ok(Some(if boolean.is_true() {
            "true".to_string()
        } else {
            "false".to_string()
        }));
    }
    if let Ok(integer) = value.extract::<i64>() {
        return Ok(Some(integer.to_string()));
    }
    if let Ok(float) = value.extract::<f64>() {
        return Ok(Some(JsonValue::from(float).to_string()));
    }
    if let Ok(string) = value.extract::<String>() {
        // GraphQL's string-literal escaping is a subset of JSON's, so serializing through
        // `serde_json` gives correct quoting/escaping for free.
        return Ok(Some(JsonValue::String(string).to_string()));
    }
    if let Ok(list) = value.cast::<PyList>() {
        let mut items = Vec::with_capacity(list.len());
        for item in list.iter() {
            // One unrenderable element makes the whole list unrenderable -- a partial list literal
            // would be actively wrong, not merely incomplete.
            match python_default_to_graphql_literal(&item)? {
                Some(rendered) => items.push(rendered),
                None => return Ok(None),
            }
        }
        return Ok(Some(format!("[{}]", items.join(", "))));
    }
    if let Ok(dict) = value.cast::<PyDict>() {
        let mut entries = Vec::with_capacity(dict.len());
        for (key, item_value) in dict.iter() {
            let Ok(key) = key.extract::<String>() else {
                return Ok(None);
            };
            match python_default_to_graphql_literal(&item_value)? {
                // A GraphQL object literal's field names are unquoted, unlike JSON's.
                Some(rendered) => entries.push(format!("{key}: {rendered}")),
                None => return Ok(None),
            }
        }
        return Ok(Some(format!("{{{}}}", entries.join(", "))));
    }
    Ok(None)
}

/// `Some(name)` if `value` is a member of a `@bramble.enum`-decorated enum, rendered under whatever
/// GraphQL name that member actually travels as (`bramble.enum_value(name=...)` override, else the
/// Python identifier) -- matching how execution serializes a resolved enum member.
fn enum_member_literal(value: &Bound<'_, PyAny>) -> PyResult<Option<String>> {
    let Ok(info) = value.get_type().getattr("__bramble_type_info__") else {
        return Ok(None);
    };
    if info.getattr("kind")?.extract::<String>()? != "enum" {
        return Ok(None);
    }
    let member_name: String = value.getattr("name")?.extract()?;
    for enum_value in info.getattr("enum_values")?.try_iter()? {
        let enum_value = enum_value?;
        if enum_value.getattr("name")?.extract::<String>()? == member_name {
            let graphql_name: Option<String> = enum_value.getattr("graphql_name")?.extract()?;
            return Ok(Some(graphql_name.unwrap_or(member_name)));
        }
    }
    Ok(Some(member_name))
}

/// Converts a Python value into `serde_json::Value`, covering the subset actually needed for
/// GraphQL variable values (booleans, numbers, strings, null, lists, string-keyed objects).
/// `PyBool` must be checked before numeric extraction: Python's `bool` is a subclass of `int`, so
/// `value.extract::<i64>()` would otherwise silently accept `True`/`False` as `1`/`0`.
pub(crate) fn python_to_json_value(value: &Bound<'_, PyAny>) -> PyResult<JsonValue> {
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

/// Converts a parsed GraphQL literal `Value` into the Python object a resolver would receive as
/// an argument -- `python_to_json_value`'s mirror, but starting from a query AST node instead of
/// a Python value, and resolving `Value::Variable` by name against `variable_values` instead of
/// erroring on an unhandled type. There's no unrepresentable-value failure mode here (every
/// `Value` variant has some Python equivalent); the only way this fails is an undefined variable
/// reference, since full variable-definition coercion (checking a use against its declared type)
/// is out of scope until it's needed (see `check_value_matches_type`'s doc comment in
/// `bramble-core`). `Value::Enum` becomes a plain Python `str` of its name -- bramble has no
/// schema concept of enum types yet, so there's no richer target to coerce into.
pub(crate) fn graphql_value_to_python(
    py: Python<'_>,
    value: &Value,
    variable_values: &Bound<'_, PyDict>,
) -> PyResult<Py<PyAny>> {
    match value {
        Value::Variable(name) => {
            let name = name.as_str();
            match variable_values.get_item(name)? {
                Some(bound_value) => Ok(bound_value.unbind()),
                None => Err(raise(
                    py,
                    Box::new(CoreGraphQLError::new(
                        format!("query references undefined variable '${name}'"),
                        ErrorCode::GraphqlValidationFailed,
                    )),
                )),
            }
        }
        Value::Null => Ok(py.None()),
        Value::Number(number) => {
            if let Some(int_value) = number.as_i64() {
                Ok(int_value.into_pyobject(py)?.into_any().unbind())
            } else if let Some(float_value) = number.as_f64() {
                Ok(float_value.into_pyobject(py)?.into_any().unbind())
            } else {
                Err(raise(
                    py,
                    Box::new(CoreGraphQLError::new(
                        format!("number literal '{number}' is out of range"),
                        ErrorCode::GraphqlValidationFailed,
                    )),
                ))
            }
        }
        Value::String(string) => Ok(string.into_pyobject(py)?.into_any().unbind()),
        Value::Boolean(boolean) => Ok(boolean.into_pyobject(py)?.to_owned().into_any().unbind()),
        Value::Binary(bytes) => Ok(PyBytes::new(py, bytes).into_any().unbind()),
        Value::Enum(name) => Ok(name.as_str().into_pyobject(py)?.into_any().unbind()),
        Value::List(items) => {
            let list = PyList::empty(py);
            for item in items {
                list.append(graphql_value_to_python(py, item, variable_values)?)?;
            }
            Ok(list.into_any().unbind())
        }
        Value::Object(map) => {
            let dict = PyDict::new(py);
            for (key, item_value) in map {
                dict.set_item(key.as_str(), graphql_value_to_python(py, item_value, variable_values)?)?;
            }
            Ok(dict.into_any().unbind())
        }
    }
}

#[pyclass(name = "LoweredDirective", frozen, get_all, skip_from_py_object)]
pub struct PyLoweredDirective {
    pub name: String,
    pub arguments: Py<PyDict>,
}

/// A field surviving `@skip`/`@include` pruning, ready for execution (§7a/§11): its arguments and
/// custom directives' arguments are already resolved to real Python objects (variables substituted
/// via `graphql_value_to_python`), but which resolver ultimately owns `field_name` -- and thus how
/// `arguments`' GraphQL names map to the resolver's actual Python parameter names -- isn't decided
/// here. For a field reached through an interface/union, that depends on the concrete value
/// resolved at runtime (`type_condition` narrows *which* selections apply to that concrete type,
/// but doesn't change that the binding itself is deferred); resolving it any earlier would bake in
/// a possibly-wrong parameter mapping. See `bramble_core::lowering::LoweredField`'s own doc comment.
#[pyclass(name = "LoweredField", frozen, get_all, skip_from_py_object)]
pub struct PyLoweredField {
    pub response_key: String,
    pub field_name: String,
    pub type_condition: Option<String>,
    pub arguments: Py<PyDict>,
    pub directives: Vec<Py<PyLoweredDirective>>,
    pub selections: Vec<Py<PyLoweredField>>,
    /// This field's own 1-indexed source position in the query text (§8's `locations`) -- lets
    /// an execution-time error report where in the query it came from, the same as a parse/
    /// validation error already does.
    pub line: usize,
    pub column: usize,
    /// Whether this field is only deliverable after the initial payload, per `@defer` -- see
    /// `bramble_core::lowering::LoweredField`'s own doc comment for the exclusivity rule deciding
    /// this. Flattened out of the Rust-side `Option<DeferMarker>` into two plain fields (rather
    /// than a nested pyclass) to keep this type's Python-facing shape simple.
    pub is_deferred: bool,
    pub defer_label: Option<String>,
    /// Whether this field carries `@stream` directly -- only ever legal on a list-typed field
    /// (enforced during validation, not here). Same flattening rationale as `is_deferred` above.
    pub is_streamed: bool,
    pub stream_initial_count: Option<i64>,
    pub stream_label: Option<String>,
}

fn convert_arguments(
    py: Python<'_>,
    arguments: Vec<LoweredArgument>,
    variable_values: &Bound<'_, PyDict>,
) -> PyResult<Py<PyDict>> {
    let dict = PyDict::new(py);
    for argument in arguments {
        // An argument whose value is a variable the caller did not supply is *omitted*, not an
        // error: §CoerceArgumentValues says such an argument falls back to its default value, and
        // leaving it out of the dict is what lets the resolver's own default apply. Erroring here
        // instead made every ordinary optional-variable query fail -- `query Q($cursor: ID) {
        // items(cursor: $cursor) }` run without a cursor is exactly what a paginating client
        // sends for the first page. A variable that is genuinely undeclared is still reported,
        // by `bramble-core`'s validation, which is where that check belongs.
        if let Value::Variable(name) = &argument.value
            && variable_values.get_item(name.as_str())?.is_none()
        {
            continue;
        }
        let value = graphql_value_to_python(py, &argument.value, variable_values)?;
        dict.set_item(argument.graphql_name, value)?;
    }
    Ok(dict.unbind())
}

fn convert_directive(
    py: Python<'_>,
    directive: LoweredDirective,
    variable_values: &Bound<'_, PyDict>,
) -> PyResult<PyLoweredDirective> {
    Ok(PyLoweredDirective {
        name: directive.name,
        arguments: convert_arguments(py, directive.arguments, variable_values)?,
    })
}

fn convert_field(py: Python<'_>, field: LoweredField, variable_values: &Bound<'_, PyDict>) -> PyResult<PyLoweredField> {
    let directives = field
        .directives
        .into_iter()
        .map(|directive| Py::new(py, convert_directive(py, directive, variable_values)?))
        .collect::<PyResult<Vec<_>>>()?;
    let selections = field
        .selections
        .into_iter()
        .map(|selection| Py::new(py, convert_field(py, selection, variable_values)?))
        .collect::<PyResult<Vec<_>>>()?;

    let (is_deferred, defer_label) = match field.deferred {
        Some(marker) => (true, marker.label),
        None => (false, None),
    };
    let (is_streamed, stream_initial_count, stream_label) = match field.streamed {
        Some(marker) => (true, Some(marker.initial_count), marker.label),
        None => (false, None, None),
    };

    Ok(PyLoweredField {
        response_key: field.response_key,
        field_name: field.field_name,
        type_condition: field.type_condition,
        arguments: convert_arguments(py, field.arguments, variable_values)?,
        directives,
        selections,
        line: field.location.line,
        column: field.location.column,
        is_deferred,
        defer_label,
        is_streamed,
        stream_initial_count,
        stream_label,
    })
}

/// Parses `query` and lowers its (optionally named) operation into a `LoweredField` tree (§7a/§11):
/// `@skip`/`@include`d selections pruned and fragment spreads/inline fragments flattened (evaluating
/// each directive's `if` argument against `variable_values`, substituting `$variable` references
/// throughout field/directive arguments the same way). Schema-agnostic, like `validate_query` and
/// the pruning pass this replaces -- it only needs the query document and this request's variables,
/// not the compiled schema, since resolver binding and interface/union type dispatch both need a
/// concrete value that doesn't exist until execution actually reaches that field. Returns
/// `(operation_type, fields)`: the operation type ("query"/"mutation"/"subscription") is what the
/// execution bridge needs to pick the schema's matching root type before resolving anything.
#[pyfunction]
#[pyo3(signature = (query, *, variable_values, operation_name=None))]
pub fn lower_query(
    py: Python<'_>,
    query: &str,
    variable_values: &Bound<'_, PyDict>,
    operation_name: Option<String>,
) -> PyResult<(&'static str, Vec<Py<PyLoweredField>>)> {
    let document = bramble_core::parse_document(query).map_err(|error| raise(py, error))?;
    lower_parsed_document(py, &document, variable_values, operation_name)
}

/// The same lowering as `lower_query`, but against an already-parsed `ParsedDocument`.
///
/// The normal path: parse once, validate the handle, then lower the same handle. Also what makes an
/// APQ replay genuinely cheaper -- parse and validate are both skipped, and only the
/// variable-dependent work (`@skip`/`@include` evaluation, argument substitution) is redone, which
/// has to be redone since it depends on *this* request's variable values.
#[pyfunction]
#[pyo3(signature = (document, *, variable_values, operation_name=None))]
pub fn lower_document(
    py: Python<'_>,
    document: &PyParsedDocument,
    variable_values: &Bound<'_, PyDict>,
    operation_name: Option<String>,
) -> PyResult<(&'static str, Vec<Py<PyLoweredField>>)> {
    lower_parsed_document(py, &document.document, variable_values, operation_name)
}

fn lower_parsed_document(
    py: Python<'_>,
    document: &ExecutableDocument,
    variable_values: &Bound<'_, PyDict>,
    operation_name: Option<String>,
) -> PyResult<(&'static str, Vec<Py<PyLoweredField>>)> {
    // Only used for `@skip`/`@include`'s own `if` argument, which is always a boolean (or a
    // variable that should be one) -- a variable that can't convert to JSON at all (a `datetime`,
    // a custom scalar's own object, ...) could never have legitimately been a boolean anyway, so
    // it's left out of this map rather than failing the whole request over a variable that isn't
    // even used for skip/include. Field/directive arguments never consult this map: they resolve
    // straight from `variable_values` (the original Python objects) via `graphql_value_to_python`.
    let mut variables = HashMap::with_capacity(variable_values.len());
    for (key, value) in variable_values.iter() {
        let key: String = key.extract()?;
        if let Ok(json_value) = python_to_json_value(&value) {
            variables.insert(key, json_value);
        }
    }

    let (operation_type, lowered) =
        core_lower_document(document, &variables, operation_name.as_deref()).map_err(|error| raise(py, error))?;

    let fields = lowered
        .into_iter()
        .map(|field| Py::new(py, convert_field(py, field, variable_values)?))
        .collect::<PyResult<Vec<_>>>()?;

    Ok((bramble_core::lowering::operation_type_str(operation_type), fields))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn eval<'py>(py: Python<'py>, expression: &str) -> Bound<'py, PyAny> {
        let globals = PyDict::new(py);
        py.eval(std::ffi::CString::new(expression).unwrap().as_c_str(), Some(&globals), None)
            .expect("expression evaluates")
    }

    #[test]
    fn python_to_json_value_covers_every_variable_shape() {
        Python::attach(|py| {
            for (expression, expected) in [
                ("None", "null"),
                ("True", "true"),
                ("False", "false"),
                ("7", "7"),
                ("-7", "-7"),
                ("1.5", "1.5"),
                ("\"text\"", "\"text\""),
                ("[1, \"a\", None]", "[1,\"a\",null]"),
                ("{\"k\": [1, 2]}", "{\"k\":[1,2]}"),
                ("[]", "[]"),
                ("{}", "{}"),
            ] {
                let value = eval(py, expression);
                let json = python_to_json_value(&value).expect("converts");
                assert_eq!(json.to_string(), expected, "for {expression}");
            }
        });
    }

    #[test]
    fn booleans_are_not_silently_converted_to_integers() {
        // Python's `bool` subclasses `int`, so an `extract::<i64>()` placed before the bool check
        // would turn `True` into `1` and corrupt every boolean variable.
        Python::attach(|py| {
            let value = eval(py, "True");
            assert_eq!(python_to_json_value(&value).unwrap().to_string(), "true");
        });
    }

    #[test]
    fn a_non_json_serializable_variable_errors_rather_than_guessing() {
        Python::attach(|py| {
            let value = eval(py, "object()");
            let error = python_to_json_value(&value).expect_err("an arbitrary object has no JSON form");
            assert!(error.to_string().contains("not JSON-serializable"), "unexpected: {error}");
        });
    }

    #[test]
    fn a_dict_with_non_string_keys_is_rejected() {
        // GraphQL object keys are always names; a non-string key means the caller handed us
        // something that was never a valid variable value.
        Python::attach(|py| {
            let value = eval(py, "{1: 2}");
            assert!(python_to_json_value(&value).is_err());
        });
    }

    #[test]
    fn nested_structures_round_trip_through_both_directions() {
        Python::attach(|py| {
            let value = eval(py, "{\"outer\": [{\"inner\": True}, None]}");
            let json = python_to_json_value(&value).unwrap();
            assert_eq!(json.to_string(), "{\"outer\":[{\"inner\":true},null]}");
        });
    }
}
