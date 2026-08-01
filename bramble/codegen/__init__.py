#
# This source file is part of the Bramble open source project.
#
# Copyright (c) 2026 Jaldis B.V.
#
# Licensed under the MIT OR Apache-2.0 license (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://opensource.org/licenses/MIT
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

"""Generates typed code (Python dataclasses, TypeScript types) from a `.graphql` query file
resolved against a `bramble.Schema` -- powers `bramble codegen`, but also usable directly
(`bramble.codegen.generate_operation`).

**Known, deliberate scope limits** (not silent gaps -- see `bramble/codegen/query_codegen.py`'s
own docstrings for exactly where each applies):
- No interface/union type-conditional codegen yet: a field scoped to `... on ConcreteType { ... }`
  is flattened the same as an unconditional selection, rather than generating a proper
  discriminated-union response type per concrete type. Works correctly as long as a query against
  an interface/union field only selects fields common to every possible type (no `... on X`).
- GraphQL enum types can't appear at all yet, since bramble's schema layer has no enum concept of
  its own (a long-standing, separately-tracked gap unrelated to codegen specifically).
"""

from bramble.codegen.plugins import PythonPlugin, QueryCodegenPlugin, TypeScriptPlugin, get_builtin_plugin  # noqa: F401
from bramble.codegen.query_codegen import QueryCodegenError, generate_operation  # noqa: F401
from bramble.codegen.types import (  # noqa: F401
    CodegenType,
    ListType,
    NamedType,
    ObjectField,
    ObjectType,
    Operation,
    OptionalType,
    VariableDefinition,
)
