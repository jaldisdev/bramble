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

use crate::compiled_schema::PyCompiledSchema;

/// Renders `schema` as GraphQL SDL (§6/§9/§12) -- see `bramble_core::sdl::render_sdl` for the
/// actual rendering logic and its documented gaps (no argument default *values*, no `enum`
/// support yet).
#[pyfunction]
pub fn render_sdl(schema: &PyCompiledSchema) -> String {
    bramble_core::sdl::render_sdl(&schema.schema)
}
