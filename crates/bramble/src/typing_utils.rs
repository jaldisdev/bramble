use bramble_core::schema::GraphQLType;
use pyo3::prelude::*;
use pyo3::sync::PyOnceLock;
use pyo3::types::PyDict;

use crate::union_info::describe_union;

/// `bramble._lazy`'s own contents, imported once per process and reused from then on -- every one
/// of these is looked up on every single type reference bramble resolves (`named_type_name` runs
/// once per leaf type across the whole schema; `seed_lazy_namespace_for_*` once per class/
/// function), so re-importing the module and re-doing the attribute lookups each time would be
/// pure waste: `sys.modules` already caches the import itself, but repeating the lookup still
/// costs real (if small) per-call overhead -- `PyOnceLock` skips that entirely after the first
/// call, for the lifetime of the process.
struct LazyModuleCache {
    lazy_type: Py<PyAny>,
    namespace_for_class: Py<PyAny>,
    namespace_for_callable: Py<PyAny>,
}

static LAZY_MODULE_CACHE: PyOnceLock<LazyModuleCache> = PyOnceLock::new();

fn lazy_module_cache(py: Python<'_>) -> PyResult<&LazyModuleCache> {
    LAZY_MODULE_CACHE.get_or_try_init(py, || {
        let module = py.import("bramble._lazy")?;
        Ok(LazyModuleCache {
            lazy_type: module.getattr("LazyType")?.unbind(),
            namespace_for_class: module.getattr("namespace_for_class")?.unbind(),
            namespace_for_callable: module.getattr("namespace_for_callable")?.unbind(),
        })
    })
}

/// The `LazyType` class object, cached -- shared by `named_type_name` (below) and
/// `union_info.rs::describe_union`'s own union-member check, so both recognize the exact same
/// placeholder without either needing its own separate import.
pub(crate) fn lazy_type_class(py: Python<'_>) -> PyResult<&Bound<'_, PyAny>> {
    Ok(lazy_module_cache(py)?.lazy_type.bind(py))
}

/// Merges `bramble._lazy.namespace_for_class(cls)`'s result into `localns` -- every `get_type_hints`
/// call site needs this done before it runs, or a `bramble.lazy(...)`-tagged forward reference
/// raises `NameError` before ever getting a chance to resolve to its `LazyType` placeholder
/// instead of the (not yet safely importable) real class.
pub(crate) fn seed_lazy_namespace_for_class(
    py: Python<'_>,
    cls: &Bound<'_, PyAny>,
    localns: &Bound<'_, PyDict>,
) -> PyResult<()> {
    let namespace = lazy_module_cache(py)?.namespace_for_class.bind(py).call1((cls,))?;
    localns.update(namespace.cast::<PyDict>()?.as_mapping())
}

/// Same as `seed_lazy_namespace_for_class`, for a single function's own annotations (a resolver
/// method or a standalone operation-directive function).
pub(crate) fn seed_lazy_namespace_for_callable(
    py: Python<'_>,
    func: &Bound<'_, PyAny>,
    localns: &Bound<'_, PyDict>,
) -> PyResult<()> {
    let namespace = lazy_module_cache(py)?.namespace_for_callable.bind(py).call1((func,))?;
    localns.update(namespace.cast::<PyDict>()?.as_mapping())
}

/// The handful of external type objects `resolve_core`/`is_union_origin` compare every type
/// reference against, at every level of `Optional[...]`/`Annotated[...]` unwrapping -- cached for
/// the same reason as `LazyModuleCache` above.
struct OriginCache {
    typing_union: Py<PyAny>,
    types_union_type: Py<PyAny>,
    async_generator: Py<PyAny>,
    async_iterator: Py<PyAny>,
    async_iterable: Py<PyAny>,
}

static ORIGIN_CACHE: PyOnceLock<OriginCache> = PyOnceLock::new();

fn origin_cache(py: Python<'_>) -> PyResult<&OriginCache> {
    ORIGIN_CACHE.get_or_try_init(py, || {
        let collections_abc = py.import("collections.abc")?;
        Ok(OriginCache {
            typing_union: py.import("typing")?.getattr("Union")?.unbind(),
            types_union_type: py.import("types")?.getattr("UnionType")?.unbind(),
            async_generator: collections_abc.getattr("AsyncGenerator")?.unbind(),
            async_iterator: collections_abc.getattr("AsyncIterator")?.unbind(),
            async_iterable: collections_abc.getattr("AsyncIterable")?.unbind(),
        })
    })
}

/// `typing.get_origin(int | None)` and `typing.get_origin(typing.Optional[int])` both denote a
/// union, but which singleton object represents "union" isn't guaranteed stable across Python
/// versions (some versions unify `types.UnionType` and `typing.Union`, some don't) -- so this
/// checks identity against both rather than assuming either alone is sufficient.
pub fn is_union_origin(py: Python<'_>, origin: &Bound<'_, PyAny>) -> PyResult<bool> {
    let cache = origin_cache(py)?;
    if origin.is(cache.typing_union.bind(py)) {
        return Ok(true);
    }
    Ok(origin.is(cache.types_union_type.bind(py)))
}

/// Unwraps `Annotated[T, ...]` to `(T, metadata)`. Non-`Annotated` annotations pass through
/// unchanged with empty metadata.
pub fn unwrap_annotated<'py>(
    typing: &Bound<'py, PyAny>,
    annotation: Bound<'py, PyAny>,
) -> PyResult<(Bound<'py, PyAny>, Vec<Bound<'py, PyAny>>)> {
    let origin = typing.call_method1("get_origin", (&annotation,))?;
    let annotated = typing.getattr("Annotated")?;
    if !origin.is(&annotated) {
        return Ok((annotation, Vec::new()));
    }

    let args: Vec<Bound<PyAny>> = typing
        .call_method1("get_args", (&annotation,))?
        .try_iter()?
        .collect::<PyResult<_>>()?;
    let mut args = args.into_iter();
    let underlying = args.next().expect("Annotated[T, ...] always has an underlying type");

    Ok((underlying, args.collect()))
}

/// Finds the first item in `metadata` that's an instance of `marker_class`, if any.
pub fn find_marker<'py>(
    metadata: &[Bound<'py, PyAny>],
    marker_class: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyAny>>> {
    for value in metadata {
        if value.is_instance(marker_class)? {
            return Ok(Some(value.clone()));
        }
    }
    Ok(None)
}

/// Resolves a Python type annotation to a `GraphQLType`. Handles `Optional[T]`/`T | None`
/// (nullable, no `NonNull` wrapper), `list[T]`/`List[T]` (`GraphQLType::List`), and
/// `Annotated[T, ...]` (metadata is irrelevant to the *type*, just unwrapped). Anything not
/// explicitly wrapped nullable is `NonNull` by default, matching how Python's own convention
/// (no `| None` means required) already lines up with GraphQL's.
pub fn resolve_graphql_type(
    py: Python<'_>,
    typing: &Bound<'_, PyAny>,
    annotation: &Bound<'_, PyAny>,
) -> PyResult<GraphQLType> {
    let (core, nullable) = resolve_core(py, typing, annotation)?;
    if nullable {
        Ok(core)
    } else {
        Ok(GraphQLType::NonNull(Box::new(core)))
    }
}

/// Returns `(core_type, is_nullable_at_this_level)` -- the type as it would be if this level
/// turns out nullable, without the `NonNull` wrapper `resolve_graphql_type` adds when it isn't.
fn resolve_core(
    py: Python<'_>,
    typing: &Bound<'_, PyAny>,
    annotation: &Bound<'_, PyAny>,
) -> PyResult<(GraphQLType, bool)> {
    let origin = typing.call_method1("get_origin", (annotation,))?;

    let annotated = typing.getattr("Annotated")?;
    if origin.is(&annotated) {
        let args: Vec<Bound<PyAny>> = typing.call_method1("get_args", (annotation,))?.try_iter()?.collect::<PyResult<_>>()?;
        let underlying = args[0].clone();
        let metadata = &args[1..];
        let underlying_origin = typing.call_method1("get_origin", (&underlying,))?;

        let union_marker_class = py.import("bramble._union")?.getattr("UnionDefinition")?;
        let has_union_marker = find_marker(metadata, &union_marker_class)?.is_some();

        if is_union_origin(py, &underlying_origin)? || has_union_marker {
            // A union's real name can come from its own `bramble.union(...)` marker -- unlike
            // other `Annotated[...]` metadata (irrelevant to the type itself), this one has to
            // survive into `resolve_union`'s own `describe_union` call, or the marker's explicit
            // name is silently lost in favor of an autogenerated one (stripping to `underlying`
            // and recursing, as below, would drop the metadata before it's ever consulted).
            // `has_union_marker` alone (without `underlying` itself being a real `Union[...]`)
            // covers a *single*-member bramble union: Python collapses a one-member `Union[X]`
            // straight down to plain `X`, so `Annotated[X, bramble.union(...)]` (no `Union[...]`
            // wrapper at all) is the only way one can be spelled.
            return resolve_union(py, typing, annotation, &underlying);
        }
        return resolve_core(py, typing, &underlying);
    }

    if is_union_origin(py, &origin)? {
        return resolve_union(py, typing, annotation, annotation);
    }

    // A subscription resolver is an async generator, typed `AsyncGenerator[T, None]` (or
    // `AsyncIterator[T]`/`AsyncIterable[T]`) per spec/convention -- the field's own GraphQL type
    // is `T` itself, not the generator wrapper, since bramble's execution layer only ever consumes
    // the annotation this way once, at schema-build time (the actual per-event iteration and
    // "is this really an async generator at runtime" check both happen in Python at execution
    // time -- see `bramble._execution.subscribe_async`, which has no need to know the annotation).
    let cache = origin_cache(py)?;
    if origin.eq(cache.async_generator.bind(py))?
        || origin.eq(cache.async_iterator.bind(py))?
        || origin.eq(cache.async_iterable.bind(py))?
    {
        let args = typing.call_method1("get_args", (annotation,))?;
        let element = args.get_item(0)?;
        return resolve_core(py, typing, &element);
    }

    if origin.eq(py.get_type::<pyo3::types::PyList>())? || origin.eq(py.get_type::<pyo3::types::PyTuple>())? {
        let args = typing.call_method1("get_args", (annotation,))?;
        let element = args.get_item(0)?;
        let inner = resolve_graphql_type(py, typing, &element)?;
        return Ok((GraphQLType::List(Box::new(inner)), false));
    }

    // A leaf/named type.
    Ok((GraphQLType::Named(named_type_name(py, annotation)?), false))
}

/// Resolves a union annotation: `describe_annotation` is whatever should be handed to
/// `describe_union` (the full `Annotated[Union[...], marker]` form when there's a marker to
/// preserve, otherwise the same as `union_annotation`), while `union_annotation` is always the
/// plain `Union[...]` form `typing.get_args` can read members off of.
fn resolve_union(
    py: Python<'_>,
    typing: &Bound<'_, PyAny>,
    describe_annotation: &Bound<'_, PyAny>,
    union_annotation: &Bound<'_, PyAny>,
) -> PyResult<(GraphQLType, bool)> {
    let union_origin = typing.call_method1("get_origin", (union_annotation,))?;
    if !is_union_origin(py, &union_origin)? {
        // `union_annotation` isn't a real `typing.Union` at all -- the single-member bramble
        // union case (see `resolve_core`'s own comment on why that's spelled without a
        // `Union[...]` wrapper). Never `Optional[...]`: that's always a real `Union[T, None]`,
        // which has a real union origin and so never reaches this branch.
        let union_info = describe_union(py, describe_annotation)?;
        return Ok((GraphQLType::Named(union_info.name), false));
    }

    let members: Vec<Bound<PyAny>> = typing
        .call_method1("get_args", (union_annotation,))?
        .try_iter()?
        .collect::<PyResult<_>>()?;
    let none_type = py.None().into_bound(py).get_type();
    let non_none: Vec<Bound<PyAny>> = members.into_iter().filter(|member| !member.is(&none_type)).collect();

    if non_none.len() == 1 {
        // Optional[T]: nullable, using T's own core (T's own nullability marker, if T were
        // itself somehow also optional, is irrelevant -- Optional[...] already established
        // nullable at this level).
        let (core, _) = resolve_core(py, typing, &non_none[0])?;
        return Ok((core, true));
    }

    // A genuine multi-member union with no `None` (a bramble union, most likely) -- a single
    // named type by its own registered/autogenerated name, non-null unless wrapped in
    // Optional[...] at an outer level.
    let union_info = describe_union(py, describe_annotation)?;
    Ok((GraphQLType::Named(union_info.name), false))
}

/// The builtin/stdlib scalar type objects `named_type_name` compares every leaf type reference
/// against -- cached for the same reason as `LazyModuleCache`/`OriginCache` above: this runs once
/// per leaf type across the *whole* schema (every field, every argument, every list/optional
/// wrapping bottoms out here), so re-importing five separate modules on every single call would
/// be pure waste.
struct BuiltinScalarCache {
    str_type: Py<PyAny>,
    bool_type: Py<PyAny>,
    int_type: Py<PyAny>,
    float_type: Py<PyAny>,
    id_type: Py<PyAny>,
    datetime_type: Py<PyAny>,
    date_type: Py<PyAny>,
    time_type: Py<PyAny>,
    decimal_type: Py<PyAny>,
    uuid_type: Py<PyAny>,
}

static BUILTIN_SCALAR_CACHE: PyOnceLock<BuiltinScalarCache> = PyOnceLock::new();

fn builtin_scalar_cache(py: Python<'_>) -> PyResult<&BuiltinScalarCache> {
    BUILTIN_SCALAR_CACHE.get_or_try_init(py, || {
        let builtins = py.import("builtins")?;
        let datetime_module = py.import("datetime")?;
        Ok(BuiltinScalarCache {
            str_type: builtins.getattr("str")?.unbind(),
            bool_type: builtins.getattr("bool")?.unbind(),
            int_type: builtins.getattr("int")?.unbind(),
            float_type: builtins.getattr("float")?.unbind(),
            id_type: py.import("bramble")?.getattr("ID")?.unbind(),
            datetime_type: datetime_module.getattr("datetime")?.unbind(),
            date_type: datetime_module.getattr("date")?.unbind(),
            time_type: datetime_module.getattr("time")?.unbind(),
            decimal_type: py.import("decimal")?.getattr("Decimal")?.unbind(),
            uuid_type: py.import("uuid")?.getattr("UUID")?.unbind(),
        })
    })
}

pub(crate) fn named_type_name(py: Python<'_>, annotation: &Bound<'_, PyAny>) -> PyResult<String> {
    // A `bramble.lazy(...)`-tagged forward reference resolves (once `localns` has been seeded --
    // see `namespace_for_class`/`namespace_for_callable`) to this placeholder instead of the real
    // class, precisely so no import is needed yet: its own name is already everything a field's
    // type signature needs.
    if annotation.is_instance(lazy_type_class(py)?)? {
        return annotation.getattr("type_name")?.extract();
    }

    let cache = builtin_scalar_cache(py)?;
    if annotation.is(cache.str_type.bind(py)) {
        return Ok("String".to_string());
    }
    if annotation.is(cache.bool_type.bind(py)) {
        // Must be checked before `int`: Python's `bool` is a subclass of `int`, but identity
        // comparison against the exact `int`/`bool` class objects below isn't affected by that
        // subclass relationship either way -- this ordering is just for readability.
        return Ok("Boolean".to_string());
    }
    if annotation.is(cache.int_type.bind(py)) {
        return Ok("Int".to_string());
    }
    if annotation.is(cache.float_type.bind(py)) {
        return Ok("Float".to_string());
    }

    if annotation.is(cache.id_type.bind(py)) {
        return Ok("ID".to_string());
    }

    if annotation.is(cache.datetime_type.bind(py)) {
        return Ok("DateTime".to_string());
    }
    if annotation.is(cache.date_type.bind(py)) {
        return Ok("Date".to_string());
    }
    if annotation.is(cache.time_type.bind(py)) {
        return Ok("Time".to_string());
    }
    if annotation.is(cache.decimal_type.bind(py)) {
        return Ok("Decimal".to_string());
    }
    if annotation.is(cache.uuid_type.bind(py)) {
        return Ok("UUID".to_string());
    }

    if let Ok(info) = annotation.getattr("__bramble_type_info__") {
        return info.getattr("name")?.extract();
    }

    // A custom scalar's `NewType` (or any other otherwise-unrecognized type): its GraphQL name
    // isn't knowable here (scalar registration happens later, at `Schema()`, via
    // `config.scalar_map`) -- fall back to the type's own `__name__`, which is also the
    // convention `bramble.scalar(name=...)` follows by default (a `Base64 = NewType("Base64",
    // bytes)` typically registers a scalar named "Base64" to match).
    if let Ok(name_attr) = annotation.getattr("__name__")
        && let Ok(name) = name_attr.extract::<String>()
    {
        return Ok(name);
    }

    annotation.str()?.extract()
}
