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

use bramble_core::schema::{
    AppliedDirective, ArgumentDefinition, EnumValueDefinition, FieldDefinition, GraphQLType, TypeDefinition, TypeKind,
};
use pyo3::create_exception;
use pyo3::exceptions::{PyException, PyNameError};
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyType};

use crate::lowering::{python_default_to_graphql_literal, python_to_json_value};
use crate::resolver_binding::bind_resolver_arguments;
use crate::typing_utils::{
    find_marker, is_maybe_annotation, resolve_graphql_type, seed_lazy_namespace_for_class, unwrap_annotated,
};

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
    /// The default rendered as a GraphQL literal, or `None` when the argument has no default (or
    /// has one with no faithful literal spelling) -- introspection reports this verbatim as
    /// `__InputValue.defaultValue`, which the spec defines as a string holding the literal.
    pub default_value: Option<String>,
    pub description: Option<String>,
    pub deprecation_reason: Option<String>,
    /// Declared as `Maybe[T]` -- execution wraps a supplied value in `Some(...)`.
    pub is_maybe: bool,
}

pub(crate) fn convert_argument(py: Python<'_>, argument: ArgumentDefinition) -> PyResult<PyArgumentInfo> {
    Ok(PyArgumentInfo {
        name: argument.name,
        graphql_name: argument.graphql_name,
        is_nullable: argument.graphql_type.is_nullable(),
        graphql_type: argument.graphql_type.to_sdl_string(),
        type_info: Py::new(py, convert_graphql_type(py, &argument.graphql_type)?)?,
        has_default: argument.has_default,
        default_value: argument.default_value,
        description: argument.description,
        deprecation_reason: argument.deprecation_reason,
        is_maybe: argument.is_maybe,
    })
}

#[pyclass(name = "FieldInfo", frozen, get_all, skip_from_py_object)]
pub struct PyFieldInfo {
    pub name: String,
    pub graphql_name: Option<String>,
    pub graphql_type: String,
    pub type_info: Py<PyGraphQLType>,
    pub description: Option<String>,
    pub deprecation_reason: Option<String>,
    /// The field's default as a GraphQL literal -- populated for an input object's fields, which
    /// is the only place GraphQL allows one. `None` otherwise.
    pub default_value: Option<String>,
    /// Declared as `Maybe[T]` -- execution wraps a supplied value in `Some(...)`.
    pub is_maybe: bool,
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
        deprecation_reason: field.deprecation_reason,
        default_value: field.default_value,
        is_maybe: field.is_maybe,
        has_resolver: field.has_resolver,
        parent_parameter: field.parent_parameter,
        info_parameter: field.info_parameter,
        arguments,
    })
}

#[pyclass(name = "EnumValueInfo", frozen, get_all, skip_from_py_object)]
pub struct PyEnumValueInfo {
    pub name: String,
    pub graphql_name: Option<String>,
    pub description: Option<String>,
    pub deprecation_reason: Option<String>,
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
    /// This enum's members, empty for every other `kind` -- execution reads it to map a resolved
    /// Python enum member onto the GraphQL name a response should carry, and the reverse for an
    /// incoming argument value.
    #[pyo3(get)]
    pub enum_values: Vec<Py<PyEnumValueInfo>>,
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
fn read_fields(py: Python<'_>, cls: &Bound<'_, PyType>, allow_unresolved_annotations: bool) -> PyResult<Vec<FieldDefinition>> {
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
    // all.
    //
    // A `NameError` is therefore reported rather than swallowed, *unless* the caller has already
    // established that this is the unresolvable-forever case. Usually it is not: a field
    // forward-referencing a type defined *later in the same module* is unresolvable at decoration
    // time and perfectly resolvable once the module finishes importing. Reporting it as a "could
    // not resolve" `SchemaError` is what hands the class to `_type._PENDING_TYPES`, which retries
    // from `Schema()` and only falls back here with `allow_unresolved_annotations` once no further
    // progress is possible.
    //
    // Swallowing it unconditionally -- which is what this did before -- substituted an empty hint
    // set, silently degrading *every* field on the class to its raw annotation text. Under
    // `from __future__ import annotations` that text is a string for all of them, so a single
    // unresolvable forward reference yielded a whole type of fields named `str!` and `'User'!`:
    // invalid SDL, and a dangling type reference nothing downstream rejected.
    let cls_name: String = cls.getattr("__name__")?.extract()?;
    let localns = PyDict::new(py);
    localns.set_item(&cls_name, cls)?;
    seed_lazy_namespace_for_class(py, cls.as_any(), &localns)?;
    let kwargs = PyDict::new(py);
    kwargs.set_item("localns", &localns)?;
    kwargs.set_item("include_extras", true)?;
    let resolved_hints = match typing.call_method("get_type_hints", (cls,), Some(&kwargs)) {
        Ok(hints) => hints.cast::<PyDict>().cloned().unwrap_or_else(|_| PyDict::new(py)),
        Err(error) if error.is_instance_of::<PyNameError>(py) => {
            if !allow_unresolved_annotations {
                return Err(SchemaError::new_err(format!(
                    "could not resolve field annotations for '{cls_name}': {error}"
                )));
            }
            PyDict::new(py)
        }
        Err(error) => return Err(error),
    };

    let private_marker_class = py.import("bramble._private")?.getattr("PrivateMarker")?;

    let mut fields = Vec::new();
    for dataclass_field in dataclass_fields.try_iter()? {
        let dataclass_field = dataclass_field?;
        let name: String = dataclass_field.getattr("name")?.extract()?;
        let raw_type = dataclass_field.getattr("type")?;
        let resolved_type = resolved_hints.get_item(&name)?.unwrap_or_else(|| raw_type.clone());

        // A `bramble.field(...)`-created field always has a `.resolver` attribute (even when it's
        // `None` -- `Field.__init__` sets it unconditionally), unlike a plain dataclass field from
        // a bare annotation. That's the same distinguishing check `has_resolver` below reuses.
        let is_bramble_field = dataclass_field.getattr("resolver").is_ok();

        let (_, metadata) = unwrap_annotated(&typing, resolved_type.clone())?;
        if find_marker(&metadata, &private_marker_class)?.is_some() {
            if is_bramble_field {
                let cls_name: String = cls.getattr("__name__")?.extract()?;
                return Err(SchemaError::new_err(format!(
                    "field '{name}' on '{cls_name}' cannot be both Private and a bramble.field(...) \
                     -- either remove the Private annotation or the field configuration"
                )));
            }
            // A private plain field stays a normal Python/dataclass attribute (untouched here) --
            // it's simply never turned into a `FieldDefinition`, so it's invisible to the GraphQL
            // schema (SDL, query validation, execution) entirely.
            continue;
        }

        let graphql_name: Option<String> = dataclass_field
            .getattr("graphql_name")
            .ok()
            .and_then(|value| value.extract().ok());
        let description: Option<String> = dataclass_field
            .getattr("description")
            .ok()
            .and_then(|value| value.extract().ok());
        let deprecation_reason: Option<String> = dataclass_field
            .getattr("deprecation_reason")
            .ok()
            .and_then(|value| value.extract().ok());
        // An input object field's default. Read from `.default` only, never `.default_factory`:
        // calling a factory at schema-build time to render a literal would run arbitrary user code
        // for a documentation string, and could differ from what a request actually gets.
        // `dataclasses.MISSING` means no default, and is not a value any literal should render for.
        let is_maybe = is_maybe_annotation(py, &typing, &resolved_type)?;
        let dataclasses = py.import("dataclasses")?;
        let missing = dataclasses.getattr("MISSING")?;
        let default_value = match dataclass_field.getattr("default") {
            Ok(default) if !default.is(&missing) => python_default_to_graphql_literal(&default)?,
            _ => None,
        };
        // A `Maybe[T]` field's Python default of `None` means "omitted", not "defaults to null".
        // Rendering `= null` would tell clients the server substitutes null when the field is left
        // out, which is precisely the distinction `Maybe` exists to preserve.
        let default_value = if is_maybe { None } else { default_value };

        // `bramble.field(graphql_type=...)` replaces the annotation-derived type outright, letting
        // a field expose a type its Python annotation can't express.
        let type_override = dataclass_field.getattr("graphql_type").ok().filter(|value| !value.is_none());
        let graphql_type = match &type_override {
            Some(override_annotation) => resolve_graphql_type(py, &typing, override_annotation)?,
            None => resolve_graphql_type(py, &typing, &resolved_type)?,
        };

        let resolver = dataclass_field
            .getattr("resolver")
            .unwrap_or_else(|_| py.None().into_bound(py));
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

        fields.push(FieldDefinition {
            name,
            graphql_name,
            graphql_type,
            description,
            deprecation_reason,
            default_value,
            is_maybe,
            has_resolver,
            parent_parameter,
            info_parameter,
            arguments,
            applied_directives,
        });
    }
    Ok(fields)
}

/// Builds `cls`'s `TypeDefinition`, tagged by `kind`. Called by `bramble._type._process_type`
/// once per decorated class, after it has already been turned into a dataclass -- cross-type
/// validation (interface field contracts, directive locations) is deferred to `Schema()`
/// (Task 8b), since it needs the whole type graph, not just this class.
#[pyfunction]
#[pyo3(signature = (cls, *, kind, name=None, description=None, directives, one_of=false, allow_unresolved_annotations=false))]
#[allow(clippy::too_many_arguments)]
pub fn process_type(
    py: Python<'_>,
    cls: &Bound<'_, PyType>,
    kind: &str,
    name: Option<String>,
    description: Option<String>,
    directives: &Bound<'_, PyAny>,
    one_of: bool,
    allow_unresolved_annotations: bool,
) -> PyResult<PyTypeInfo> {
    let applied_directives = extract_applied_directives(directives)?;

    let type_kind = parse_kind(kind)?;
    let fields = read_fields(py, cls, allow_unresolved_annotations)?;

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
        enum_values: Vec::new(), // only ever populated by `process_enum`
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
        enum_values: Vec::new(),
        definition,
    })
}

/// Builds an enum's `TypeDefinition` from a Python `enum.Enum` subclass, called by
/// `bramble._enum.enum` once per decorated class -- the enum counterpart to `process_type`.
///
/// A member's GraphQL name is its Python *identifier* (`Color.RED` -> `RED`), not its value: a
/// GraphQL enum travels by member name, and the value stays a private Python detail a resolver can
/// use however it likes. `bramble.enum_value(...)` overrides that name (and adds description/
/// deprecation/directives) by being assigned as the member's value, which is why each member is
/// checked for one here before falling back to the plain identifier.
#[pyfunction]
#[pyo3(signature = (cls, *, name=None, description=None, directives))]
pub fn process_enum(
    py: Python<'_>,
    cls: &Bound<'_, PyType>,
    name: Option<String>,
    description: Option<String>,
    directives: &Bound<'_, PyAny>,
) -> PyResult<PyTypeInfo> {
    let enum_meta = py.import("enum")?.getattr("EnumMeta")?;
    if !cls.is_instance(&enum_meta)? {
        return Err(SchemaError::new_err(format!(
            "'{}' is not an enum -- @bramble.enum can only decorate an enum.Enum subclass",
            cls.getattr("__name__")?.extract::<String>()?
        )));
    }

    let applied_directives = extract_applied_directives(directives)?;
    let value_marker_class = py.import("bramble._enum")?.getattr("EnumValueDefinition")?;

    let mut enum_values = Vec::new();
    for member in cls.try_iter()? {
        let member = member?;
        let member_name: String = member.getattr("name")?.extract()?;
        let member_value = member.getattr("value")?;

        let (graphql_name, value_description, deprecation_reason, value_directives) =
            if member_value.is_instance(&value_marker_class)? {
                (
                    member_value.getattr("graphql_name")?.extract::<Option<String>>()?,
                    member_value.getattr("description")?.extract::<Option<String>>()?,
                    member_value.getattr("deprecation_reason")?.extract::<Option<String>>()?,
                    extract_applied_directives(&member_value.getattr("directives")?)?,
                )
            } else {
                (None, None, None, Vec::new())
            };

        enum_values.push(EnumValueDefinition {
            name: member_name,
            graphql_name,
            description: value_description,
            deprecation_reason,
            applied_directives: value_directives,
        });
    }

    let resolved_name = match name {
        Some(name) => name,
        None => cls.getattr("__name__")?.extract()?,
    };

    let definition = TypeDefinition {
        kind: TypeKind::Enum,
        name: resolved_name,
        description,
        one_of: false,
        fields: Vec::new(),
        applied_directives,
        interfaces: Vec::new(),
        enum_values,
    };

    let enum_values_info = definition
        .enum_values
        .iter()
        .map(|value| {
            Py::new(
                py,
                PyEnumValueInfo {
                    name: value.name.clone(),
                    graphql_name: value.graphql_name.clone(),
                    description: value.description.clone(),
                    deprecation_reason: value.deprecation_reason.clone(),
                },
            )
        })
        .collect::<PyResult<Vec<_>>>()?;

    Ok(PyTypeInfo {
        kind: "enum".to_string(),
        name: definition.name.clone(),
        description: definition.description.clone(),
        one_of: false,
        fields: Vec::new(),
        enum_values: enum_values_info,
        definition,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Builds a class from `setup` and runs **this crate's** `process_type` over it, returning the
    /// resulting `TypeDefinition`.
    ///
    /// Deliberately not going through the `@bramble.type` decorator: that calls `process_type` in
    /// the *separately compiled* extension module on `sys.path`, whose `PyTypeInfo` is a different
    /// Rust type than this test binary's, so its result cannot be extracted here (it fails with the
    /// memorable "'TypeInfo' object is not an instance of 'TypeInfo'"). Running the Python-side
    /// preparation the decorator does, then calling our own `process_type`, tests the code in this
    /// crate rather than the installed copy of it.
    fn definition(py: Python<'_>, setup: &str, class_name: &str, kind: &str) -> PyResult<TypeDefinition> {
        let globals = PyDict::new(py);
        crate::test_support::ensure_bramble_importable(py);
        py.run(std::ffi::CString::new(setup).unwrap().as_c_str(), Some(&globals), None)?;

        let cls = globals.get_item(class_name)?.unwrap();
        let bramble_type = py.import("bramble._type")?;
        bramble_type.call_method1("_ensure_field_annotations", (&cls,))?;
        let dataclasses = py.import("dataclasses")?;
        let kwargs = PyDict::new(py);
        kwargs.set_item("kw_only", true)?;
        let cls = dataclasses.call_method("dataclass", (&cls,), Some(&kwargs))?;
        bramble_type.call_method1("_restore_resolvers", (&cls,))?;

        let cls = cls.cast::<PyType>()?;
        let info = process_type(
            py,
            cls,
            kind,
            None,
            None,
            &pyo3::types::PyTuple::empty(py).into_any(),
            false,
            false,
        )?;
        Ok(info.definition)
    }

    #[test]
    fn reads_field_names_types_and_nullability() {
        Python::attach(|py| {
            let def = definition(
                py,
                "import bramble\nclass T:\n    name: str\n    nickname: str | None\n    tags: list[str]\n",
                "T",
                "type",
            )
            .unwrap();

            let rendered: Vec<(String, String)> = def
                .fields
                .iter()
                .map(|field| (field.name.clone(), field.graphql_type.to_sdl_string()))
                .collect();
            assert_eq!(
                rendered,
                vec![
                    ("name".to_string(), "String!".to_string()),
                    ("nickname".to_string(), "String".to_string()),
                    ("tags".to_string(), "[String!]!".to_string()),
                ]
            );
        });
    }

    #[test]
    fn a_private_field_is_excluded_from_the_schema_entirely() {
        Python::attach(|py| {
            let def = definition(
                py,
                "import bramble\nclass T:\n    shown: str\n    hidden: bramble.Private[str]\n",
                "T",
                "type",
            )
            .unwrap();

            let names: Vec<&str> = def.fields.iter().map(|field| field.name.as_str()).collect();
            assert_eq!(names, vec!["shown"], "a Private field must not become a FieldDefinition");
        });
    }

    #[test]
    fn a_field_name_override_survives_onto_the_definition() {
        Python::attach(|py| {
            let def = definition(
                py,
                "import bramble\nclass T:\n    internal: str = bramble.field(name='publicName', default='x')\n",
                "T",
                "type",
            )
            .unwrap();

            // The type name comes from the decorator's own `name=`, which this helper bypasses;
            // what matters here is that the *field* override reaches the definition.
            assert_eq!(def.fields[0].name, "internal", "the Python attribute name is unchanged");
            assert_eq!(def.fields[0].graphql_name.as_deref(), Some("publicName"));
        });
    }

    #[test]
    fn an_input_type_rejects_a_field_with_a_resolver() {
        Python::attach(|py| {
            let error = definition(
                py,
                "import bramble\nclass T:\n    @bramble.field\n    def computed() -> str:\n        return 'x'\n",
                "T",
                "input",
            )
            .expect_err("an input field cannot resolve");
            assert!(error.to_string().contains("cannot declare a resolver"), "unexpected: {error}");
        });
    }

    #[test]
    fn interfaces_are_collected_from_the_mro_in_order() {
        Python::attach(|py| {
            let def = definition(
                py,
                "import bramble\n@bramble.interface\nclass Node:\n    id: str\n@bramble.interface\nclass Timestamped(Node):\n    at: str\nclass T(Timestamped):\n    name: str\n",
                "T",
                "type",
            )
            .unwrap();

            // MRO order, transitive parents included -- `implements Timestamped & Node`.
            assert_eq!(def.interfaces, vec!["Timestamped".to_string(), "Node".to_string()]);
        });
    }

    #[test]
    fn an_input_field_default_is_read_but_an_object_field_default_is_not_rendered() {
        Python::attach(|py| {
            let def = definition(
                py,
                "import bramble\nclass T:\n    limit: int = 10\n    cursor: str | None = None\n",
                "T",
                "input",
            )
            .unwrap();

            let defaults: Vec<Option<&str>> = def.fields.iter().map(|field| field.default_value.as_deref()).collect();
            assert_eq!(defaults, vec![Some("10"), Some("null")]);
        });
    }

    #[test]
    fn a_resolvers_arguments_are_classified_off_its_annotations() {
        Python::attach(|py| {
            let def = definition(
                py,
                "import bramble\nclass T:\n    @bramble.field\n    def greet(parent: bramble.Parent['T'], info: bramble.Info, name: str, shout: bool = False) -> str:\n        return name\n",
                "T",
                "type",
            )
            .unwrap();

            let field = &def.fields[0];
            // `Parent`/`Info` are bramble injections, never GraphQL arguments.
            assert_eq!(field.parent_parameter.as_deref(), Some("parent"));
            assert_eq!(field.info_parameter.as_deref(), Some("info"));
            let arguments: Vec<(&str, bool)> = field.arguments.iter().map(|a| (a.name.as_str(), a.has_default)).collect();
            assert_eq!(arguments, vec![("name", false), ("shout", true)]);
        });
    }

    #[test]
    fn an_unannotated_self_parameter_gets_the_targeted_parent_hint() {
        Python::attach(|py| {
            let error = definition(
                py,
                "import bramble\nclass T:\n    @bramble.field\n    def greet(self) -> str:\n        return 'x'\n",
                "T",
                "type",
            )
            .expect_err("an unannotated self must be rejected");
            assert!(error.to_string().contains("Parent[T]"), "unexpected: {error}");
        });
    }
}
