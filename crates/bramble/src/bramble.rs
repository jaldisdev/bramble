use pyo3::prelude::*;

mod compiled_schema;
mod error;
mod lowering;
mod operation_directive_info;
mod persisted_query;
mod resolver_binding;
mod schema_directive_info;
mod type_info;
mod typing_utils;
mod union_info;
mod validation;

#[pymodule]
fn _bramble(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(type_info::process_type, module)?)?;
    module.add_class::<type_info::PyTypeInfo>()?;
    module.add_class::<type_info::PyFieldInfo>()?;
    module.add_class::<type_info::PyArgumentInfo>()?;
    module.add_class::<type_info::PyGraphQLType>()?;
    module.add(
        "SchemaError",
        module.py().get_type::<type_info::SchemaError>(),
    )?;
    module.add("GraphQLError", module.py().get_type::<error::GraphQLError>())?;
    module.add_function(wrap_pyfunction!(union_info::describe_union, module)?)?;
    module.add_class::<union_info::PyUnionInfo>()?;
    module.add_function(wrap_pyfunction!(
        schema_directive_info::describe_schema_directive,
        module
    )?)?;
    module.add_class::<schema_directive_info::PySchemaDirectiveInfo>()?;
    module.add_class::<schema_directive_info::PyDirectiveFieldInfo>()?;
    module.add_function(wrap_pyfunction!(lowering::lower_query, module)?)?;
    module.add_class::<lowering::PyLoweredField>()?;
    module.add_class::<lowering::PyLoweredDirective>()?;
    module.add_function(wrap_pyfunction!(
        operation_directive_info::describe_operation_directive,
        module
    )?)?;
    module.add_class::<operation_directive_info::PyOperationDirectiveInfo>()?;
    module.add_function(wrap_pyfunction!(compiled_schema::compile_schema, module)?)?;
    module.add_class::<compiled_schema::PyCompiledSchema>()?;
    module.add_function(wrap_pyfunction!(validation::validate_query, module)?)?;
    module.add_function(wrap_pyfunction!(persisted_query::resolve_persisted_query, module)?)?;
    Ok(())
}
