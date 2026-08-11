# Query codegen

`bramble codegen` generates typed client-side code (Python dataclasses or
TypeScript types, by default) from a `.graphql` query file, resolved
against a real `bramble.Schema` -- so a client's own request/response
shapes stay in sync with the schema they're actually querying against.

```bash
bramble codegen get_post.graphql --schema myapp.schema:schema -o generated/ -p python
```

```graphql
# get_post.graphql
query GetPost($slug: String!) {
  postBySlug(slug: $slug) {
    title
    author {
      name
    }
  }
}
```

```python
# generated/GetPost.py
from __future__ import annotations
import dataclasses

@dataclasses.dataclass
class GetPostResultPostBySlugAuthor:
    name: str

@dataclasses.dataclass
class GetPostResultPostBySlug:
    title: str
    author: GetPostResultPostBySlugAuthor

@dataclasses.dataclass
class GetPostResult:
    postBySlug: GetPostResultPostBySlug | None

@dataclasses.dataclass
class GetPostVariables:
    slug: str
```

Pass `-p typescript` (or both, repeatably) for the TypeScript equivalent:

```typescript
export type GetPostResultPostBySlugAuthor = {
  name: string;
};

export type GetPostResultPostBySlug = {
  title: string;
  author: GetPostResultPostBySlugAuthor;
};

export type GetPostResult = {
  postBySlug: GetPostResultPostBySlug | null;
};

export type GetPostVariables = {
  slug: string;
};
```

## CLI usage

```bash
bramble codegen QUERY [QUERY ...] --schema SCHEMA -o OUTPUT_DIR -p PLUGIN [-p PLUGIN ...]
```

- `QUERY` -- one or more `.graphql` file paths (each must contain exactly
  one named operation).
- `--schema` -- a `module.path:symbol` selector, same as `bramble dev`
  (see [Tools](../guides/tools.md)).
- `-o`/`--output-dir` -- directory to write generated files into, one
  `<OperationName>.<extension>` per query/plugin combination.
- `-p`/`--plugin` -- `python`, `typescript`, or `module.path:ClassName`
  for a project-specific plugin (repeatable).

## Programmatic use

```python
from bramble.codegen import generate_operation, PythonPlugin

operation = generate_operation(schema, query_text)
code = PythonPlugin().generate_code(operation)
```

`generate_operation(schema, query_text) -> Operation` parses and validates
the query against `schema`, then walks its selection set (and every
variable's own input type) into an `Operation` IR: a `result_type`, a
`variables` list, and `nested_types` for every other shape reached along
the way (nested result objects, and input types reachable from variables).

## Writing a custom plugin

A plugin is a `QueryCodegenPlugin` subclass implementing `generate_code`:

```python
from bramble.codegen.plugins import QueryCodegenPlugin
from bramble.codegen.types import Operation

class MyPlugin(QueryCodegenPlugin):
    file_extension = "txt"

    def generate_code(self, operation: Operation) -> str:
        return f"# {operation.name}\n"
```

Use it via `-p mymodule:MyPlugin`, or directly:
`bramble codegen get_post.graphql --schema myapp.schema:schema -o generated/ -p mymodule:MyPlugin`.

## Known scope limits

- **No interface/union type-conditional codegen yet** -- a field scoped to
  `... on ConcreteType { ... }` is flattened the same as an unconditional
  selection, rather than generating a discriminated-union response type
  per concrete type. This works correctly as long as a query against an
  interface/union field only selects fields common to every possible
  concrete type (no `... on X`).
- **Enums are generated as `str`/`string`, not as a named type** -- an
  [enum](../types/enums.md) travels as its member name, so this is accurate
  for the value actually on the wire, but a generated shape doesn't
  constrain it to the enum's own members. Generating a real Python `enum`
  (or a TypeScript string-literal union) is a refinement not yet made.
