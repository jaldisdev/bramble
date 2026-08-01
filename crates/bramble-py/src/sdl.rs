use pyo3::prelude::*;

use crate::compiled_schema::PyCompiledSchema;

/// Renders `schema` as GraphQL SDL (§6/§9/§12) -- see `bramble_core::sdl::render_sdl` for the
/// actual rendering logic and its documented gaps (no argument default *values*, no `enum`
/// support yet).
#[pyfunction]
pub fn render_sdl(schema: &PyCompiledSchema) -> String {
    bramble_core::sdl::render_sdl(&schema.schema)
}
