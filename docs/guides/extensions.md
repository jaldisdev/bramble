# Extensions

Extensions let you hook into bramble without modifying your schema. There are
two kinds, and they are independent:

- **`SchemaExtension`** wraps a whole request — parse, validate, execute — and
  optionally every field resolution within it. Registered with
  `Schema(extensions=[...])`. Use for tracing, logging, error masking, caching
  whole responses.
- **`FieldExtension`** wraps one field's resolver. Registered with
  `bramble.field(extensions=[...])`. Use for per-field authorization, memoising
  a specific field, reshaping arguments.

bramble aims to be close to API-compatible with Strawberry, so both classes
match Strawberry's hook names and signatures. Deviations are listed at the end,
each with its reason.

---

## `SchemaExtension`

Every lifecycle hook is a **generator that yields exactly once**. Code before
the `yield` runs before the step; code after runs once it finishes. Both sync
and async generators are supported.

```python
import time

class Timing(bramble.SchemaExtension):
    def on_operation(self):
        start = time.perf_counter()
        yield
        self.execution_context.extensions_results["timing"] = {
            "ms": (time.perf_counter() - start) * 1000
        }

schema = bramble.Schema(query=Query, extensions=[Timing])
```

An extension is registered as a **class or an instance**. A class is
instantiated once per request, which is what makes per-request state (a start
time, a counter) safe; an instance is reused as-is, and is your responsibility
to keep concurrency-safe.

### Hooks

| Hook | Wraps |
| --- | --- |
| `on_operation` | the entire request: parse, validate, execute, and result assembly |
| `on_parse` | turning query text into a parsed document |
| `on_validate` | validating that document against the schema |
| `on_execute` | resolving the operation's fields |
| `on_stream_result(result)` | each payload yielded by `execute_incremental` / `subscribe_async` |
| `resolve(next_, source, info, **kwargs)` | *every* field resolution in the request |
| `get_results()` | returns a dict merged into the response's `extensions` key |

Nesting, outermost first:

```
on_operation
├── on_parse
├── on_validate
└── on_execute
    └── resolve  (per field, innermost)
```

### Ordering with several extensions

Hooks nest as context managers in list order — **onion ordering**. The first
extension's "before" code runs first and its "after" code runs *last*:

```python
Schema(query=Query, extensions=[A, B])
```

```
A: before
  B: before
    ... the step ...
  B: after
A: after
```

This matches Strawberry, and is the ordering you want for tracing: the
outermost extension's span genuinely contains the inner ones.

### Errors

Hooks are entered through an `AsyncExitStack`, so if the wrapped step raises —
or an inner hook raises on the way in — **every already-entered hook is
unwound**, in reverse order, before the exception propagates.

Unwinding means the exception is thrown into the hook *at its `yield`*, exactly
as for any context manager. So code after a bare `yield` does **not** run on
failure. A hook that must clean up regardless says so:

```python
class Cleanup(bramble.SchemaExtension):
    def on_operation(self):
        resource = acquire()
        try:
            yield
        finally:
            resource.release()   # runs however the request ends
```

This is the same shape as `DependencyScope.aclose()` and the subscription
generator teardown elsewhere in bramble, and the same semantics Strawberry
has, since both build on `contextlib`.

A hook may also deliberately **suppress** an error by catching it around the
`yield` — that's how an error-masking extension is written. When an
`on_operation` hook suppresses a failure there is no result to return, so the
response falls back to the spec's minimal `{"data": None}` shape.

### The execution context

Each hook can reach the request through `self.execution_context`:

```python
class ExecutionContext:
    query: str | None                     # the raw query text, None for a persisted replay
    operation_name: str | None
    variable_values: dict[str, Any]
    context: Any                          # whatever was passed as context=
    root_value: Any
    operation_type: str | None            # "query"/"mutation"/"subscription", None before parsing
    schema: Schema
    result: dict[str, Any] | None         # the response, available in on_operation's "after" half
    errors: list[GraphQLError]            # accumulated field errors
    extensions_results: dict[str, Any]    # merged into the response's "extensions" key
```

---

## `FieldExtension`

A field extension wraps a single field's resolver. `next_` is the rest of the
chain — the next extension, or the resolver itself at the end.

```python
class UpperCase(bramble.FieldExtension):
    async def resolve_async(self, next_, source, info, **kwargs):
        return (await next_(source, info, **kwargs)).upper()

@bramble.type
class Query:
    @bramble.field(extensions=[UpperCase])
    def greeting() -> str:
        return "hello"
```

Two more hooks, both optional:

- `apply(field)` — called once at schema-build time with the `bramble.Field`,
  for an extension that wants to inspect or adjust the field's configuration.
- `map_arguments(kwargs)` — reshapes the resolver's arguments after coercion,
  before the chain runs.

### Short-circuiting

An extension that doesn't call `next_` never runs the resolver. This is the
authorization case:

```python
class RequiresRole(bramble.FieldExtension):
    def __init__(self, role: str) -> None:
        self.role = role

    async def resolve_async(self, next_, source, info, **kwargs):
        if self.role not in info.context["roles"]:
            raise bramble.GraphQLError("Forbidden", code=bramble.ErrorCode.FIELD_RESOLUTION_FAILED)
        return await next_(source, info, **kwargs)
```

A raised `GraphQLError` becomes an ordinary field error and obeys the usual
null-propagation rules, exactly like one raised from the resolver.

### Ordering

Field extensions compose in list order, onion style — `extensions=[A, B]` means
`A` wraps `B` wraps the resolver.

Relative to everything else on a field, innermost outward:

```
resolver
└── field extensions          (list order, A outermost)
    └── permission_classes    (checked before any of this runs)
        └── operation directives   (@upper etc., transform the returned value)
            └── SchemaExtension.resolve
```

Concretely, for one field:

1. `permission_classes` are checked. A denial stops here; nothing below runs.
2. `SchemaExtension.resolve` hooks wrap everything below, in list order.
3. Field extensions wrap the resolver, in list order.
4. The resolver runs.
5. Custom **operation directives** from the query transform the *returned
   value*.

**Field extensions run inside the directive chain, not outside it.** A field
extension wraps the act of *producing* the value; an operation directive
transforms the value the client asked to have transformed. So a caching field
extension caches the resolver's own output, not some client's `@upper`-ed view
of it — which is the only ordering that makes caching correct.

---

## Deviations from Strawberry

1. **`resolve` and `resolve_async` are both accepted; there is no
   `SyncToAsyncExtension` and no sync/async mixing error.** Strawberry needs
   that machinery because it has separate sync and async executors, and a sync
   executor cannot await an async extension. bramble has exactly one execution
   path and it is async, so whichever method an extension defines is called and
   its result awaited if awaitable. A Strawberry extension defining either
   method (or both) ports unchanged; one relying on the `TypeError` for a bad
   sync/async mix will simply not see it, because the situation cannot arise.

2. **`SchemaExtension.on_parse` and `on_validate` wrap genuinely separate
   steps, which requires splitting bramble's current pipeline.** Today
   `validate_query()` parses *and* validates in one Rust call, and
   `lower_query()` parses again — so there is no parse boundary to hook. The
   implementation will expose the already-existing parsed-document handle
   (`PersistedDocument`, generalised to `ParsedDocument`) plus
   `parse_query()` / `validate_document()` / `lower_document()`, so the
   pipeline becomes parse → validate → lower → execute.

   This was a prerequisite, not a bonus: without it `on_parse` and `on_validate`
   would have fired around the same call and lied about what they wrap. It also
   removes a double parse that every request used to pay.

   An APQ replay skips both parse and validate — the document was validated when
   it was registered — so neither hook fires for one. That's deliberate: an
   extension should see what actually happened, not a phantom span.

3. **`get_results()` is sync-or-async, and its output lands under the response's
   `extensions` key.** Same as Strawberry. The key is omitted entirely when no
   extension reports anything, rather than appearing as an empty object.

   `get_results()` runs *after* `on_operation`'s "after" half, so the usual
   timing-extension shape — measure across the `yield`, record afterwards —
   reaches the response.

4. **`LifecycleStep` is not ported.** It exists in Strawberry to tag hooks for
   its own tracing extensions; nothing in bramble consumes it.

5. **A `FieldExtension` may be given as a class or an instance.** Strawberry
   requires an instance. Extensions taking constructor arguments still must be
   instances (`extensions=[RequiresRole("admin")]`), but `extensions=[UpperCase]`
   is a natural thing to write and is accepted.

---

## Not supported in this pass

- **`Depends[T]` in extension hooks.** Dependency injection is scoped to a
  field resolution — it needs an `Info` and the request's `DependencyScope`,
  neither of which a request-level hook like `on_parse` has. Extensions get
  `execution_context.context` instead, which is where a request-scoped
  container would live anyway. `FieldExtension` hooks *do* run inside a field
  resolution and could support it later; they don't yet, and calling `next_`
  resolves the field's own dependencies normally with per-request caching
  intact.
- **Extensions that add validation rules** (Strawberry's
  `AddValidationRules`). bramble's validator is Rust-side and takes no
  pluggable rules.
- **`Schema.extensions` mutation after construction.** The field-extension
  chain is composed once at schema-build time, so adding an extension to a
  built schema has no effect.
