use bramble_core::schema::{ArgumentDefinition, FieldDefinition, TypeDefinition, TypeKind};
use pyo3::create_exception;
use pyo3::exceptions::PyException;
use pyo3::prelude::*;
use pyo3::types::PyType;

use crate::resolver_binding::bind_resolver_arguments;

create_exception!(
    _bramble,
    SchemaError,
    PyException,
    "Raised for bramble schema build-time errors."
);

#[pyclass(name = "ArgumentInfo", frozen, get_all, skip_from_py_object)]
#[derive(Clone)]
pub struct PyArgumentInfo {
    pub name: String,
    pub graphql_name: Option<String>,
    pub type_repr: Option<String>,
    pub is_nullable: bool,
    pub has_default: bool,
    pub description: Option<String>,
    pub deprecation_reason: Option<String>,
}

impl From<ArgumentDefinition> for PyArgumentInfo {
    fn from(argument: ArgumentDefinition) -> Self {
        Self {
            name: argument.name,
            graphql_name: argument.graphql_name,
            type_repr: argument.type_repr,
            is_nullable: argument.is_nullable,
            has_default: argument.has_default,
            description: argument.description,
            deprecation_reason: argument.deprecation_reason,
        }
    }
}

#[pyclass(name = "FieldInfo", frozen, get_all, skip_from_py_object)]
#[derive(Clone)]
pub struct PyFieldInfo {
    pub name: String,
    pub type_repr: Option<String>,
    pub has_resolver: bool,
    pub parent_parameter: Option<String>,
    pub info_parameter: Option<String>,
    pub arguments: Vec<PyArgumentInfo>,
}

impl From<FieldDefinition> for PyFieldInfo {
    fn from(field: FieldDefinition) -> Self {
        Self {
            name: field.name,
            type_repr: field.type_repr,
            has_resolver: field.has_resolver,
            parent_parameter: field.parent_parameter,
            info_parameter: field.info_parameter,
            arguments: field.arguments.into_iter().map(PyArgumentInfo::from).collect(),
        }
    }
}

#[pyclass(name = "TypeInfo", frozen, get_all)]
pub struct PyTypeInfo {
    pub kind: String,
    pub name: String,
    pub description: Option<String>,
    pub one_of: bool,
    pub fields: Vec<PyFieldInfo>,
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

/// Reads `cls`'s fields off `dataclasses.fields(cls)` -- by the time this runs, `bramble._type`
/// has already turned `cls` into a real dataclass, so the stdlib has already done the MRO
/// merge/override work of collecting a class's own fields plus its inherited ones in the
/// correct declaration order. A field only has `.resolver` if it's a `bramble._type.Field`
/// (dataclasses auto-creates plain `dataclasses.Field`s for bare annotated attributes, which
/// have no such attribute -- `getattr(..., "resolver", None)` treats that the same as "no
/// resolver", which is exactly what a plain data field is).
fn read_fields(py: Python<'_>, cls: &Bound<'_, PyType>) -> PyResult<Vec<FieldDefinition>> {
    let dataclass_fields = py.import("dataclasses")?.call_method1("fields", (cls,))?;

    dataclass_fields
        .try_iter()?
        .map(|dataclass_field| {
            let dataclass_field = dataclass_field?;
            let name: String = dataclass_field.getattr("name")?.extract()?;
            let type_repr = dataclass_field
                .getattr("type")?
                .str()
                .ok()
                .and_then(|s| s.extract::<String>().ok());
            let resolver = dataclass_field.getattr("resolver").unwrap_or_else(|_| py.None().into_bound(py));
            let has_resolver = !resolver.is_none();

            let (parent_parameter, info_parameter, arguments) = if has_resolver {
                let binding = bind_resolver_arguments(py, cls, &resolver)?;
                (binding.parent_parameter, binding.info_parameter, binding.arguments)
            } else {
                (None, None, Vec::new())
            };

            Ok(FieldDefinition {
                name,
                type_repr,
                has_resolver,
                parent_parameter,
                info_parameter,
                arguments,
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
    let _ = directives;

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

    let definition = TypeDefinition {
        kind: type_kind,
        name: resolved_name,
        description,
        one_of,
        fields,
    };

    Ok(PyTypeInfo {
        kind: kind.to_string(),
        name: definition.name,
        description: definition.description,
        one_of: definition.one_of,
        fields: definition.fields.into_iter().map(PyFieldInfo::from).collect(),
    })
}
