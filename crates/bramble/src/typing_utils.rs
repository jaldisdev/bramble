use pyo3::prelude::*;

/// `typing.get_origin(int | None)` and `typing.get_origin(typing.Optional[int])` both denote a
/// union, but which singleton object represents "union" isn't guaranteed stable across Python
/// versions (some versions unify `types.UnionType` and `typing.Union`, some don't) -- so this
/// checks identity against both rather than assuming either alone is sufficient.
pub fn is_union_origin(py: Python<'_>, origin: &Bound<'_, PyAny>) -> PyResult<bool> {
    let typing_union = py.import("typing")?.getattr("Union")?;
    if origin.is(&typing_union) {
        return Ok(true);
    }
    let types_union = py.import("types")?.getattr("UnionType")?;
    Ok(origin.is(&types_union))
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
