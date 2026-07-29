use bramble_core::schema::{AppliedDirective, ArgumentDefinition, FieldDefinition, GraphQLType, TypeDefinition, TypeKind};
use pyo3::create_exception;
use pyo3::exceptions::PyException;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyType};

use crate::lowering::python_to_json_value;
use crate::resolver_binding::bind_resolver_arguments;
use crate::typing_utils::{resolve_graphql_type, seed_lazy_namespace_for_class};

create_exception!(
    _bramble,
    SchemaError,
    PyException,
    "Raised for bramble schema build-time errors."
);

/// A structured mirror of `bramble_core::schema::GraphQLType`, recursive through `of_type` the
/// same way `NonNull`/`List` wrap an inner type. Exists alongside the flat SDL-string
/// `graphql_type` (kept for display/backward compatibility) because execution (Task 11) needs to
/// walk `NonNull`/`List` wrapping structurally for correct null-propagation -- a rendered
/// `"[String!]!"` can't drive that logic without re-parsing it.
#[pyclass(name = "GraphQLTypeInfo", frozen, get_all, skip_from_py_object)]
pub struct PyGraphQLType {
    pub kind: String,
    pub name: Option<String>,
    pub of_type: Option<Py<PyGraphQLType>>,
}

fn convert_graphql_type(py: Python<'_>, graphql_type: &GraphQLType) -> PyResult<PyGraphQLType> {
    match graphql_type {
        GraphQLType::Named(name) => Ok(PyGraphQLType {
            kind: "NAMED".to_string(),
            name: Some(name.clone()),
            of_type: None,
        }),
        GraphQLType::List(inner) => Ok(PyGraphQLType {
            kind: "LIST".to_string(),
            name: None,
            of_type: Some(Py::new(py, convert_graphql_type(py, inner)?)?),
        }),
        GraphQLType::NonNull(inner) => Ok(PyGraphQLType {
            kind: "NON_NULL".to_string(),
            name: None,
            of_type: Some(Py::new(py, convert_graphql_type(py, inner)?)?),
        }),
    }
}

#[pyclass(name = "ArgumentInfo", frozen, get_all, skip_from_py_object)]
pub struct PyArgumentInfo {
    pub name: String,
    pub graphql_name: Option<String>,
    pub graphql_type: String,
    pub type_info: Py<PyGraphQLType>,
    pub is_nullable: bool,
    pub has_default: bool,
    pub description: Option<String>,
    pub deprecation_reason: Option<String>,
}

pub(crate) fn convert_argument(py: Python<'_>, argument: ArgumentDefinition) -> PyResult<PyArgumentInfo> {
    Ok(PyArgumentInfo {
        name: argument.name,
        graphql_name: argument.graphql_name,
        is_nullable: argument.graphql_type.is_nullable(),
        graphql_type: argument.graphql_type.to_sdl_string(),
        type_info: Py::new(py, convert_graphql_type(py, &argument.graphql_type)?)?,
        has_default: argument.has_default,
        description: argument.description,
        deprecation_reason: argument.deprecation_reason,
    })
}

#[pyclass(name = "FieldInfo", frozen, get_all, skip_from_py_object)]
pub struct PyFieldInfo {
    pub name: String,
    pub graphql_name: Option<String>,
    pub graphql_type: String,
    pub type_info: Py<PyGraphQLType>,
    pub description: Option<String>,
    pub is_nullable: bool,
    pub has_resolver: bool,
    pub parent_parameter: Option<String>,
    pub info_parameter: Option<String>,
    pub arguments: Vec<Py<PyArgumentInfo>>,
}

pub(crate) fn convert_field(py: Python<'_>, field: FieldDefinition) -> PyResult<PyFieldInfo> {
    let arguments = field
        .arguments
        .into_iter()
        .map(|argument| Py::new(py, convert_argument(py, argument)?))
        .collect::<PyResult<Vec<_>>>()?;

    Ok(PyFieldInfo {
        name: field.name,
        graphql_name: field.graphql_name,
        is_nullable: field.graphql_type.is_nullable(),
        graphql_type: field.graphql_type.to_sdl_string(),
        type_info: Py::new(py, convert_graphql_type(py, &field.graphql_type)?)?,
        description: field.description,
        has_resolver: field.has_resolver,
        parent_parameter: field.parent_parameter,
        info_parameter: field.info_parameter,
        arguments,
    })
}

#[pyclass(name = "TypeInfo", frozen)]
pub struct PyTypeInfo {
    #[pyo3(get)]
    pub kind: String,
    #[pyo3(get)]
    pub name: String,
    #[pyo3(get)]
    pub description: Option<String>,
    #[pyo3(get)]
    pub one_of: bool,
    #[pyo3(get)]
    pub fields: Vec<Py<PyFieldInfo>>,
    /// Not Python-exposed -- the original Rust IR, kept so `Schema()` (Task 8b's graph walker)
    /// can hand already-computed `TypeInfo`s straight to `compile_schema` (Task 9) without a
    /// lossy round-trip back through the display-friendly fields above (re-parsing SDL type
    /// strings, re-deriving `TypeKind` from a plain string, etc.).
    pub definition: TypeDefinition,
}

fn parse_kind(kind: &str) -> PyResult<TypeKind> {
    match kind {
        "type" => Ok(TypeKind::Type),
        "interface" => Ok(TypeKind::Interface),
        "input" => Ok(TypeKind::Input),
        other => Err(SchemaError::new_err(format!(
            "unknown type kind '{other}' (expected 'type', 'interface', or 'input')"
        ))),
    }
}

/// Mirrors `bramble._type._validate_directive_locations`'s exact check (§6): every instance in
/// `directives` that's actually a `@bramble.schema_directive` object (anything else is silently
/// ignored) must declare `required_location` among its own locations. Needed here, in Rust,
/// because argument classification (`resolver_binding::classify_argument`) happens entirely on
/// this side of the PyO3 boundary -- unlike type-/field-level directives, which are validated in
/// Python (`_type.py`) before `process_type` ever runs, there's no equivalent Python-side pass
/// over a resolver's own arguments to do this check in first.
pub(crate) fn validate_directive_locations(
    directives: &Bound<'_, PyAny>,
    required_location: &str,
    owner_name: &str,
) -> PyResult<()> {
    for item in directives.try_iter()? {
        let item = item?;
        let Ok(info) = item.getattr("__bramble_directive_info__") else {
            continue;
        };
        let locations: Vec<String> = info.getattr("locations")?.extract()?;
        if !locations.iter().any(|location| location == required_location) {
            let name: String = info.getattr("name")?.extract()?;
            return Err(SchemaError::new_err(format!(
                "directive '@{name}' cannot be applied to '{owner_name}' ({required_location}); \
                 declared locations: {}",
                locations.join(", ")
            )));
        }
    }
    Ok(())
}

/// Converts a sequence of applied schema-directive instances (§6) into `AppliedDirective`s ready
/// for SDL rendering -- location legality is checked separately (`validate_directive_locations`,
/// or Python's own `_validate_directive_locations` for type-/field-level directives) before this
/// runs, so this is purely value extraction: for each instance that actually is a
/// `@bramble.schema_directive`-decorated object (anything else is silently ignored, matching
/// `directives=[...]`'s existing "non-directive objects are ignored" behavior), read its own
/// `__bramble_directive_info__` for the directive's name and declared fields, then read each
/// field's *value* off the instance itself and convert it the same way a resolved argument value
/// would be (`python_to_json_value`).
pub(crate) fn extract_applied_directives(directives: &Bound<'_, PyAny>) -> PyResult<Vec<AppliedDirective>> {
    let mut result = Vec::new();

    for item in directives.try_iter()? {
        let item = item?;
        let Ok(info) = item.getattr("__bramble_directive_info__") else {
            continue;
        };

        let name: String = info.getattr("name")?.extract()?;
        let mut arguments = Vec::new();
        for field in info.getattr("fields")?.try_iter()? {
            let field = field?;
            let field_name: String = field.getattr("name")?.extract()?;
            let graphql_name: Option<String> = field.getattr("graphql_name")?.extract()?;
            let key = graphql_name.unwrap_or_else(|| field_name.clone());
            let value = item.getattr(field_name.as_str())?;
            arguments.push((key, python_to_json_value(&value)?));
        }

        result.push(AppliedDirective { name, arguments });
    }

    Ok(result)
}

/// Reads `cls`'s fields off `dataclasses.fields(cls)` -- by the time this runs, `bramble._type`
/// has already turned `cls` into a real dataclass, so the stdlib has already done the MRO
/// merge/override work of collecting a class's own fields plus its inherited ones in the
/// correct declaration order. A field only has `.resolver` if it's a `bramble._type.Field`
/// (dataclasses auto-creates plain `dataclasses.Field`s for bare annotated attributes, which
/// have no such attribute -- `getattr(..., "resolver", None)` treats that the same as "no
/// resolver", which is exactly what a plain data field is).
fn read_fields(py: Python<'_>, cls: &Bound<'_, PyType>) -> PyResult<Vec<FieldDefinition>> {
    let dataclass_fields = py.import("dataclasses")?.call_method1("fields", (cls,))?;
    let typing = py.import("typing")?;

    // `dataclass_field.type` is frequently just a string (under `from __future__ import
    // annotations`, or a resolver-injected return annotation that was itself a string) --
    // `typing.get_origin("float | None")` is always None, so the structured GraphQLType can't be
    // computed from it directly. `get_type_hints` resolves the whole class's annotations (walking
    // its MRO, using each base's own module globals) into real objects; seeding `localns` with the
    // class itself handles a field forward-referencing its own enclosing type. It can't handle a
    // field referencing some *other* type defined in the same enclosing local scope (a sibling
    // class inside a test function, say) -- `get_type_hints` has no visibility into that scope at
    // all. Rather than fail type registration outright over an annotation we can't resolve, fall
    // back to an empty hint set: `resolve_graphql_type` still degrades gracefully on a raw string
    // (falls through to treating it as an opaque named type) rather than erroring.
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

    dataclass_fields
        .try_iter()?
        .map(|dataclass_field| {
            let dataclass_field = dataclass_field?;
            let name: String = dataclass_field.getattr("name")?.extract()?;
            let graphql_name: Option<String> = dataclass_field
                .getattr("graphql_name")
                .ok()
                .and_then(|value| value.extract().ok());
            let description: Option<String> = dataclass_field
                .getattr("description")
                .ok()
                .and_then(|value| value.extract().ok());
            let raw_type = dataclass_field.getattr("type")?;
            let resolved_type = resolved_hints.get_item(&name)?.unwrap_or_else(|| raw_type.clone());
            let graphql_type = resolve_graphql_type(py, &typing, &resolved_type)?;

            let resolver = dataclass_field.getattr("resolver").unwrap_or_else(|_| py.None().into_bound(py));
            let has_resolver = !resolver.is_none();

            let (parent_parameter, info_parameter, arguments) = if has_resolver {
                let binding = bind_resolver_arguments(py, cls, &resolver)?;
                (binding.parent_parameter, binding.info_parameter, binding.arguments)
            } else {
                (None, None, Vec::new())
            };

            // A plain (non-`bramble.field`) dataclass field has no `.directives` attribute --
            // `getattr`'s default handles that the same way `graphql_name`/`resolver` already do.
            let field_directives = dataclass_field
                .getattr("directives")
                .unwrap_or_else(|_| pyo3::types::PyTuple::empty(py).into_any());
            let applied_directives = extract_applied_directives(&field_directives)?;

            Ok(FieldDefinition {
                name,
                graphql_name,
                graphql_type,
                description,
                has_resolver,
                parent_parameter,
                info_parameter,
                arguments,
                applied_directives,
            })
        })
        .collect()
}

/// Builds `cls`'s `TypeDefinition`, tagged by `kind`. Called by `bramble._type._process_type`
/// once per decorated class, after it has already been turned into a dataclass -- cross-type
/// validation (interface field contracts, directive locations) is deferred to `Schema()`
/// (Task 8b), since it needs the whole type graph, not just this class.
#[pyfunction]
#[pyo3(signature = (cls, *, kind, name=None, description=None, directives, one_of=false))]
#[allow(clippy::too_many_arguments)]
pub fn process_type(
    py: Python<'_>,
    cls: &Bound<'_, PyType>,
    kind: &str,
    name: Option<String>,
    description: Option<String>,
    directives: &Bound<'_, PyAny>,
    one_of: bool,
) -> PyResult<PyTypeInfo> {
    let applied_directives = extract_applied_directives(directives)?;

    let type_kind = parse_kind(kind)?;
    let fields = read_fields(py, cls)?;

    if type_kind == TypeKind::Input
        && let Some(bad_field) = fields.iter().find(|f| f.has_resolver)
    {
        return Err(SchemaError::new_err(format!(
            "input type fields cannot declare a resolver, but field '{}' does",
            bad_field.name
        )));
    }

    let resolved_name = match name {
        Some(name) => name,
        None => cls.getattr("__name__")?.extract()?,
    };

    // MRO order (skipping `cls` itself) rather than `__bases__`: an interface implementing
    // another interface (`class FieldError(Error)`) needs its own transitive parents listed too,
    // and MRO already deduplicates diamond inheritance for free.
    let mut interfaces = Vec::new();
    for base in cls.call_method0("mro")?.try_iter()?.skip(1) {
        let base = base?;
        let Ok(info) = base.getattr("__bramble_type_info__") else {
            continue;
        };
        let base_kind: String = info.getattr("kind")?.extract()?;
        if base_kind == "interface" {
            interfaces.push(info.getattr("name")?.extract::<String>()?);
        }
    }

    let definition = TypeDefinition {
        kind: type_kind,
        name: resolved_name,
        description,
        one_of,
        fields,
        applied_directives,
        interfaces,
    };

    let fields_info = definition
        .fields
        .iter()
        .cloned()
        .map(|field| Py::new(py, convert_field(py, field)?))
        .collect::<PyResult<Vec<_>>>()?;

    Ok(PyTypeInfo {
        kind: kind.to_string(),
        name: definition.name.clone(),
        description: definition.description.clone(),
        one_of: definition.one_of,
        fields: fields_info,
        definition,
    })
}
