use pyo3::prelude::*;

mod error;
mod resolver_binding;
mod type_info;
mod typing_utils;
mod union_info;

#[pymodule]
fn _bramble(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(type_info::process_type, module)?)?;
    module.add_class::<type_info::PyTypeInfo>()?;
    module.add_class::<type_info::PyFieldInfo>()?;
    module.add_class::<type_info::PyArgumentInfo>()?;
    module.add(
        "SchemaError",
        module.py().get_type::<type_info::SchemaError>(),
    )?;
    module.add("GraphQLError", module.py().get_type::<error::GraphQLError>())?;
    module.add_function(wrap_pyfunction!(union_info::describe_union, module)?)?;
    module.add_class::<union_info::PyUnionInfo>()?;
    Ok(())
}
