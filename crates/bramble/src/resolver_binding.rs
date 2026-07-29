use bramble_core::schema::ArgumentDefinition;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyType};

use crate::type_info::SchemaError;
use crate::typing_utils::{find_marker, resolve_graphql_type, unwrap_annotated};

pub struct ResolverBinding {
    pub parent_parameter: Option<String>,
    pub info_parameter: Option<String>,
    pub arguments: Vec<ArgumentDefinition>,
}

pub(crate) fn classify_argument<'py>(
    py: Python<'py>,
    typing: &Bound<'py, PyAny>,
    parameter_name: String,
    annotation: Bound<'py, PyAny>,
    has_default: bool,
) -> PyResult<ArgumentDefinition> {
    let (underlying, metadata) = unwrap_annotated(typing, annotation)?;
    let argument_class = py.import("bramble._resolver")?.getattr("Argument")?;
    let argument_marker = find_marker(&metadata, &argument_class)?;

    let (graphql_name, description, deprecation_reason, type_override) = match &argument_marker {
        None => (None, None, None, None),
        Some(marker) => (
            marker.getattr("name")?.extract::<Option<String>>()?,
            marker.getattr("description")?.extract::<Option<String>>()?,
            marker.getattr("deprecation_reason")?.extract::<Option<String>>()?,
            {
                let graphql_type = marker.getattr("graphql_type")?;
                if graphql_type.is_none() { None } else { Some(graphql_type) }
            },
        ),
    };

    let graphql_type = match type_override {
        Some(override_annotation) => resolve_graphql_type(py, typing, &override_annotation)?,
        None => resolve_graphql_type(py, typing, &underlying)?,
    };

    Ok(ArgumentDefinition {
        name: parameter_name,
        graphql_name,
        graphql_type,
        has_default,
        description,
        deprecation_reason,
    })
}

/// Resolves every parameter's (and the return value's) annotation to a real object, keyed by
/// name. Needed because under `from __future__ import annotations` (or on Python versions where
/// it's the default), `parameter.annotation` is just a string -- `typing.get_origin("Parent[T]")`
/// is always `None`, silently misclassifying every Parent[T]/Info/Annotated[...] parameter as a
/// plain argument. `typing.get_type_hints` evaluates those strings back into real objects, but
/// only ever sees the function's own module globals -- it has no visibility into an enclosing
/// function's local scope, so a resolver that forward-references its *own* class
/// (`Parent[Circle]` inside `class Circle`) fails to resolve whenever that class is defined
/// somewhere other than module scope (as most of bramble's own tests do). Seeding `localns` with
/// the class being processed (when there is one -- a resolver always has one, a standalone
/// operation directive function never does) fixes exactly that case.
pub(crate) fn resolve_annotations<'py>(
    py: Python<'py>,
    typing: &Bound<'py, PyAny>,
    localns_seed: Option<&Bound<'py, PyType>>,
    func: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyDict>> {
    let localns = PyDict::new(py);
    if let Some(cls) = localns_seed {
        let cls_name: String = cls.getattr("__name__")?.extract()?;
        localns.set_item(cls_name, cls)?;
    }

    let kwargs = PyDict::new(py);
    kwargs.set_item("localns", &localns)?;
    kwargs.set_item("include_extras", true)?;

    let hints = typing
        .call_method("get_type_hints", (func,), Some(&kwargs))
        .map_err(|error| {
            SchemaError::new_err(format!(
                "could not resolve parameter annotations for '{}': {error}",
                func.getattr("__qualname__")
                    .and_then(|q| q.extract::<String>())
                    .unwrap_or_default(),
            ))
        })?;
    Ok(hints.cast::<PyDict>()?.clone())
}

/// Classifies a resolver's parameters per §3/§3a: `Parent[T]` -> the parent/root value,
/// `Info` -> the execution context, anything else -> a GraphQL field argument (optionally
/// carrying `Annotated[T, bramble.argument(...)]` metadata). A parameter's role is determined
/// solely by its annotation, never its name or position -- except that a completely *unannotated*
/// parameter named `self`/`root` gets a more helpful error pointing at `Parent[T]`, since that's
/// the mistake someone coming from a framework with implicit self-binding is most likely to make.
pub fn bind_resolver_arguments(
    py: Python<'_>,
    cls: &Bound<'_, PyType>,
    resolver: &Bound<'_, PyAny>,
) -> PyResult<ResolverBinding> {
    let inspect = py.import("inspect")?;
    let typing = py.import("typing")?;
    let resolver_module = py.import("bramble._resolver")?;
    let parent_class = resolver_module.getattr("Parent")?;
    let info_class = resolver_module.getattr("Info")?;
    let empty = inspect.getattr("Parameter")?.getattr("empty")?;

    let signature = inspect.call_method1("signature", (resolver,))?;
    let parameters = signature.getattr("parameters")?.call_method0("values")?;
    let resolved_hints = resolve_annotations(py, &typing, Some(cls), resolver)?;

    let mut parent_parameter: Option<String> = None;
    let mut info_parameter: Option<String> = None;
    let mut arguments = Vec::new();

    for parameter in parameters.try_iter()? {
        let parameter = parameter?;
        let parameter_name: String = parameter.getattr("name")?.extract()?;
        let raw_annotation = parameter.getattr("annotation")?;

        if raw_annotation.is(&empty) {
            if parameter_name == "self" || parameter_name == "root" {
                return Err(SchemaError::new_err(format!(
                    "resolver parameter '{parameter_name}' has no type annotation -- bramble \
                     does not bind an implicit parent value by name; annotate it as \
                     Parent[T] instead"
                )));
            }
            return Err(SchemaError::new_err(format!(
                "resolver parameter '{parameter_name}' has no type annotation; annotate it as \
                 Parent[T], Info, or a concrete argument type"
            )));
        }

        let annotation = resolved_hints
            .get_item(&parameter_name)?
            .unwrap_or(raw_annotation);
        let origin = typing.call_method1("get_origin", (&annotation,))?;

        if origin.is(&parent_class) {
            if parent_parameter.is_some() {
                return Err(SchemaError::new_err(
                    "resolver declares more than one Parent[T] parameter",
                ));
            }
            parent_parameter = Some(parameter_name);
            continue;
        }

        if annotation.is(&info_class) || origin.is(&info_class) {
            if info_parameter.is_some() {
                return Err(SchemaError::new_err("resolver declares more than one Info parameter"));
            }
            info_parameter = Some(parameter_name);
            continue;
        }

        let default = parameter.getattr("default")?;
        let has_default = !default.is(&empty);
        arguments.push(classify_argument(py, &typing, parameter_name, annotation, has_default)?);
    }

    Ok(ResolverBinding {
        parent_parameter,
        info_parameter,
        arguments,
    })
}
