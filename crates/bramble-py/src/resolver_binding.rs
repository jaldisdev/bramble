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

use bramble_core::schema::ArgumentDefinition;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyTuple, PyType};

use crate::type_info::{SchemaError, extract_applied_directives, validate_directive_locations};
use crate::typing_utils::{find_marker, resolve_graphql_type, seed_lazy_namespace_for_callable, unwrap_annotated};

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

    let (graphql_name, description, deprecation_reason, type_override, directives) = match &argument_marker {
        None => (None, None, None, None, None),
        Some(marker) => (
            marker.getattr("name")?.extract::<Option<String>>()?,
            marker.getattr("description")?.extract::<Option<String>>()?,
            marker.getattr("deprecation_reason")?.extract::<Option<String>>()?,
            {
                let graphql_type = marker.getattr("graphql_type")?;
                if graphql_type.is_none() { None } else { Some(graphql_type) }
            },
            Some(marker.getattr("directives")?),
        ),
    };

    let graphql_type = match type_override {
        Some(override_annotation) => resolve_graphql_type(py, typing, &override_annotation)?,
        None => resolve_graphql_type(py, typing, &underlying)?,
    };

    let directives = directives.unwrap_or_else(|| PyTuple::empty(py).into_any());
    validate_directive_locations(&directives, "ARGUMENT_DEFINITION", &parameter_name)?;
    let applied_directives = extract_applied_directives(&directives)?;

    Ok(ArgumentDefinition {
        name: parameter_name,
        graphql_name,
        graphql_type,
        has_default,
        description,
        deprecation_reason,
        applied_directives,
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
    seed_lazy_namespace_for_callable(py, func, &localns)?;

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

/// The result of classifying one function's parameters (a resolver, or a custom operation
/// directive) into their special-marker slots plus everything left over as GraphQL arguments.
pub(crate) struct ClassifiedParameters {
    pub parent_parameter: Option<String>,
    pub info_parameter: Option<String>,
    pub value_parameter: Option<String>,
    pub arguments: Vec<ArgumentDefinition>,
}

/// The one shared classifier behind both `bind_resolver_arguments` (§3/§3a) and
/// `describe_operation_directive` (§7): `Parent[T]` -> the parent/root value (resolvers only --
/// `parent_class` is `None` for a directive, which has no such concept), `Info` -> the execution
/// context (both), `DirectiveValue[T]` -> the field's already-resolved value (directives only --
/// `value_class` is `None` for a resolver), `Annotated[T, bramble.Depends(...)]` (§3c) -> excluded
/// from the GraphQL-visible argument list entirely (like `Parent`/`Info`, invisible to the schema),
/// anything else -> a GraphQL argument (optionally carrying `Annotated[T, bramble.argument(...)]`
/// metadata). A parameter's role is determined solely by its annotation, never its name or
/// position -- except that a completely *unannotated* parameter named `self`/`root` on a resolver
/// gets a more helpful error pointing at `Parent[T]`, since that's the mistake someone coming from
/// a framework with implicit self-binding is most likely to make.
///
/// `Depends`-marked parameters are recognized here (so their exclusion from `arguments` is
/// consistent everywhere `Info` is injectable) but otherwise ignored by this function -- the
/// actual provider callable + `use_cache` flag live on the original Python annotation, read back
/// out at runtime by `bramble._dependency`'s own mirror of this same classification rule, not
/// carried through this Rust-side IR. Unlike `Parent`/`Info` (at most one such slot per function),
/// a signature can carry many independent `Depends(...)` instances, each wrapping a live Python
/// provider callable to invoke -- that's an execution-time concern, not a schema-shape one, so it
/// has no business being threaded through `bramble_core`'s own (Python-free) schema IR.
pub(crate) fn classify_parameters<'py>(
    py: Python<'py>,
    cls: Option<&Bound<'py, PyType>>,
    func: &Bound<'py, PyAny>,
    parent_class: Option<&Bound<'py, PyAny>>,
    value_class: Option<&Bound<'py, PyAny>>,
    unannotated_hint: &str,
) -> PyResult<ClassifiedParameters> {
    let inspect = py.import("inspect")?;
    let typing = py.import("typing")?;
    let resolver_module = py.import("bramble._resolver")?;
    let info_class = resolver_module.getattr("Info")?;
    let depends_class = resolver_module.getattr("Depends")?;
    let empty = inspect.getattr("Parameter")?.getattr("empty")?;

    let signature = inspect.call_method1("signature", (func,))?;
    let parameters = signature.getattr("parameters")?.call_method0("values")?;
    let resolved_hints = resolve_annotations(py, &typing, cls, func)?;

    let mut parent_parameter: Option<String> = None;
    let mut info_parameter: Option<String> = None;
    let mut value_parameter: Option<String> = None;
    let mut arguments = Vec::new();

    for parameter in parameters.try_iter()? {
        let parameter = parameter?;
        let parameter_name: String = parameter.getattr("name")?.extract()?;
        let raw_annotation = parameter.getattr("annotation")?;

        if raw_annotation.is(&empty) {
            if parent_class.is_some() && (parameter_name == "self" || parameter_name == "root") {
                return Err(SchemaError::new_err(format!(
                    "resolver parameter '{parameter_name}' has no type annotation -- bramble \
                     does not bind an implicit parent value by name; annotate it as \
                     Parent[T] instead"
                )));
            }
            return Err(SchemaError::new_err(format!(
                "parameter '{parameter_name}' has no type annotation; annotate it as {unannotated_hint}"
            )));
        }

        let annotation = resolved_hints
            .get_item(&parameter_name)?
            .unwrap_or(raw_annotation);
        let origin = typing.call_method1("get_origin", (&annotation,))?;

        if let Some(parent_class) = parent_class
            && origin.is(parent_class)
        {
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
                return Err(SchemaError::new_err("declares more than one Info parameter"));
            }
            info_parameter = Some(parameter_name);
            continue;
        }

        let (_, metadata) = unwrap_annotated(&typing, annotation.clone())?;
        if find_marker(&metadata, &depends_class)?.is_some() {
            continue;
        }

        if let Some(value_class) = value_class
            && (annotation.is(value_class) || origin.is(value_class))
        {
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

    Ok(ClassifiedParameters {
        parent_parameter,
        info_parameter,
        value_parameter,
        arguments,
    })
}

/// Classifies a resolver's parameters per §3/§3a/§3c -- see `classify_parameters` for the shared
/// algorithm. Resolver-specific: recognizes `Parent[T]`, and reports the friendlier "annotate it
/// as Parent[T] instead" hint for an unannotated `self`/`root` parameter.
pub fn bind_resolver_arguments(
    py: Python<'_>,
    cls: &Bound<'_, PyType>,
    resolver: &Bound<'_, PyAny>,
) -> PyResult<ResolverBinding> {
    let resolver_module = py.import("bramble._resolver")?;
    let parent_class = resolver_module.getattr("Parent")?;

    let classified = classify_parameters(
        py,
        Some(cls),
        resolver,
        Some(&parent_class),
        None,
        "Parent[T], Info, Depends[T], or a concrete argument type",
    )?;

    Ok(ResolverBinding {
        parent_parameter: classified.parent_parameter,
        info_parameter: classified.info_parameter,
        arguments: classified.arguments,
    })
}
