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
