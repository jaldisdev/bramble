use pyo3::prelude::*;

mod type_info;

#[pymodule]
fn _bramble(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(type_info::process_type, module)?)?;
    module.add_class::<type_info::PyTypeInfo>()?;
    module.add_class::<type_info::PyFieldInfo>()?;
    module.add(
        "SchemaError",
        module.py().get_type::<type_info::SchemaError>(),
    )?;
    Ok(())
}
