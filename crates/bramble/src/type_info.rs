use std::collections::HashSet;

use bramble_core::schema::{FieldDefinition, TypeDefinition, TypeKind};
use pyo3::create_exception;
use pyo3::exceptions::PyException;
use pyo3::prelude::*;
use pyo3::types::PyType;

create_exception!(
    _bramble,
    SchemaError,
    PyException,
    "Raised for bramble schema build-time errors."
);

#[pyclass(name = "FieldInfo", frozen, get_all, skip_from_py_object)]
#[derive(Clone)]
pub struct PyFieldInfo {
    pub name: String,
    pub type_repr: Option<String>,
    pub has_resolver: bool,
}

impl From<FieldDefinition> for PyFieldInfo {
    fn from(field: FieldDefinition) -> Self {
        Self {
            name: field.name,
            type_repr: field.type_repr,
            has_resolver: field.has_resolver,
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

/// Iterates the `(name, value)` pairs of a mapping-like object (a real `dict`, or the
/// `mappingproxy` that `__dict__` actually is -- `mappingproxy` isn't a `dict` subclass at the
/// C level, so it can't be `cast::<PyDict>()`, but both support `.items()`).
fn mapping_items<'py>(mapping: &Bound<'py, PyAny>) -> PyResult<Vec<(String, Bound<'py, PyAny>)>> {
    mapping
        .call_method0("items")?
        .try_iter()?
        .map(|entry| {
            let entry = entry?;
            let name: String = entry.get_item(0)?.extract()?;
            let value = entry.get_item(1)?;
            Ok((name, value))
        })
        .collect()
}

/// A class's own (not inherited/merged) annotations. Can't read `__dict__["__annotations__"]`
/// directly: under PEP 649 (default since Python 3.14), annotations are computed lazily from a
/// per-class `__annotate__` function and may not be eagerly present in `__dict__` at all.
/// `inspect.get_annotations` is the version-robust stdlib way to get a class's own annotations
/// (falling back up the MRO only when the class itself defines none, same as pre-3.14).
fn own_annotations<'py>(klass: &Bound<'py, PyAny>) -> PyResult<Vec<(String, Bound<'py, PyAny>)>> {
    let annotations = klass
        .py()
        .import("inspect")?
        .call_method1("get_annotations", (klass,))?;
    mapping_items(&annotations)
}

fn is_field_instance(value: &Bound<'_, PyAny>, field_class: &Bound<'_, PyAny>) -> PyResult<bool> {
    value.is_instance(field_class)
}

fn field_resolver<'py>(value: &Bound<'py, PyAny>) -> PyResult<Option<Bound<'py, PyAny>>> {
    let resolver = value.getattr("resolver")?;
    if resolver.is_none() {
        Ok(None)
    } else {
        Ok(Some(resolver))
    }
}

fn resolver_return_type_repr(resolver: &Bound<'_, PyAny>) -> Option<String> {
    let annotations = resolver.getattr("__annotations__").ok()?;
    let items = mapping_items(&annotations).ok()?;
    let (_, return_annotation) = items.into_iter().find(|(name, _)| name == "return")?;
    return_annotation.str().ok()?.extract::<String>().ok()
}

/// Introspects a decorated class (`__mro__`, `__annotations__`, class `__dict__`) and builds
/// its `TypeDefinition`, tagged by `kind`. Called by `bramble._type._process_type` once per
/// decorated class -- cross-type validation (interface field contracts, directive locations)
/// is deferred to `Schema()` (Task 8b), since it needs the whole type graph, not just this class.
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

    let field_class = py.import("bramble._type")?.getattr("Field")?;
    let mro: Vec<Bound<PyAny>> = cls.getattr("__mro__")?.try_iter()?.collect::<PyResult<_>>()?;

    let mut fields: Vec<FieldDefinition> = Vec::new();
    let mut seen: HashSet<String> = HashSet::new();

    // Base-to-derived so a subclass's own annotation/override wins over its parent's.
    for klass in mro.iter().rev() {
        for (field_name, annotation) in own_annotations(klass)? {
            if field_name.starts_with("__") {
                continue;
            }

            let type_repr = annotation.str().ok().and_then(|s| s.extract::<String>().ok());
            let attribute = cls.getattr(field_name.as_str()).ok();
            let has_resolver = match &attribute {
                Some(value) if is_field_instance(value, &field_class)? => {
                    field_resolver(value)?.is_some()
                }
                _ => false,
            };

            fields.retain(|f| f.name != field_name);
            fields.push(FieldDefinition {
                name: field_name.clone(),
                type_repr,
                has_resolver,
            });
            seen.insert(field_name);
        }
    }

    // Method-style fields: a `Field` instance sitting directly in a class's `__dict__`
    // (via `@bramble.field` on a method) that has no matching variable annotation.
    for klass in mro.iter().rev() {
        let class_dict = klass.getattr("__dict__")?;
        for (attribute_name, value) in mapping_items(&class_dict)? {
            if seen.contains(&attribute_name) || attribute_name.starts_with("__") {
                continue;
            }
            if !is_field_instance(&value, &field_class)? {
                continue;
            }
            let Some(resolver) = field_resolver(&value)? else {
                continue;
            };

            fields.push(FieldDefinition {
                name: attribute_name.clone(),
                type_repr: resolver_return_type_repr(&resolver),
                has_resolver: true,
            });
            seen.insert(attribute_name);
        }
    }

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
