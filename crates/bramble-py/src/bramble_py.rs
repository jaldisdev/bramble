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

use pyo3::prelude::*;

mod compiled_schema;
mod error;
mod lowering;
mod operation_directive_info;
mod persisted_query;
mod query_document;
mod resolver_binding;
mod schema_directive_info;
mod sdl;
mod type_info;
mod typing_utils;
mod union_info;
mod validation;

#[pymodule]
fn _bramble(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(type_info::process_type, module)?)?;
    module.add_function(wrap_pyfunction!(type_info::process_enum, module)?)?;
    module.add_class::<type_info::PyTypeInfo>()?;
    module.add_class::<type_info::PyFieldInfo>()?;
    module.add_class::<type_info::PyArgumentInfo>()?;
    module.add_class::<type_info::PyEnumValueInfo>()?;
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
    module.add_function(wrap_pyfunction!(lowering::lower_persisted_document, module)?)?;
    module.add_class::<persisted_query::PyPersistedDocument>()?;
    module.add_class::<persisted_query::PyPersistedQueryResult>()?;
    module.add_function(wrap_pyfunction!(sdl::render_sdl, module)?)?;
    module.add_function(wrap_pyfunction!(query_document::parse_query_document, module)?)?;
    module.add_class::<query_document::PyQueryDocument>()?;
    module.add_class::<query_document::PyQueryOperation>()?;
    module.add_class::<query_document::PyQueryFragment>()?;
    module.add_class::<query_document::PyQuerySelection>()?;
    module.add_class::<query_document::PyQueryVariableDefinition>()?;
    Ok(())
}
