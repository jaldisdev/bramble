# Dependency injection

A bramble addition -- not present in every GraphQL library. `bramble.Depends` lets a resolver (or
custom operation directive) declare exactly what it needs as a typed parameter, instead of every
consumer having to know the shape of `Info.context` by convention:

```python
import bramble
from typing import Annotated, AsyncIterator

async def get_gel_client(info: bramble.Info) -> AsyncIterator[GelClient]:
    client = await create_gel_client(info.context["dsn"])
    try:
        yield client
    finally:
        await client.aclose()

@bramble.type
class Query:
    @bramble.field
    async def some_query(
        client: Annotated[GelClient, bramble.Depends(get_gel_client)],
    ) -> SomeResult:
        return await client.query(...)
```

`client: Annotated[GelClient, bramble.Depends(get_gel_client)]` is another annotation-classified
parameter kind alongside [`Parent[T]`, `Info`, and field arguments](resolvers.md) -- it's invisible
to the GraphQL schema entirely (no `client` argument appears in SDL or a query), and its value is
whatever `get_gel_client` returns, resolved fresh (or from cache -- see below) before the resolver
itself is called.

## Provider shapes

A provider passed to `bramble.Depends(...)` can be:

- A **plain function** returning the value directly.
- An **`async def`** function, awaited for its value.
- An **async-generator function** (`yield`-ing exactly one value), for setup/teardown around it,
  `try`/`finally` style -- exactly like `get_gel_client` above. The code after `yield` runs once,
  when the provider's own scope ends (see [Caching scope](#caching-scope) below).

For a value already created upstream (e.g. by middleware), the provider can be a trivial
pass-through with no teardown of its own:

```python
def get_gel_client(info: bramble.Info) -> GelClient:
    return info.context["gel_client"]
```

A resolver never needs to be declared `async def` just because a dependency it uses has an async
provider -- bramble resolves the dependency first (awaiting it, or driving a generator provider up
to its `yield`), then calls the resolver with the already-materialized value:

```python
@bramble.type
class Query:
    @bramble.field
    def sync_resolver(client: Annotated[GelClient, bramble.Depends(get_gel_client)]) -> str:
        return client.dsn  # no `await` needed here -- already resolved
```

## Works everywhere `Info` works

`Depends` (and `Info`) are recognized the same way in a resolver and in a
[custom operation directive](operation-directives.md) function -- both go through the exact same
classifier, so a directive can declare its own dependencies (and, previously unsupported, its own
`Info` parameter) exactly like a resolver:

```python
from bramble.directive import DirectiveLocation, DirectiveValue

@bramble.directive(locations=[DirectiveLocation.FIELD])
def audit_log(
    value: DirectiveValue[str],
    info: bramble.Info,
    client: Annotated[GelClient, bramble.Depends(get_gel_client)],
) -> str:
    client.log_access(info.field_name)
    return value
```

## Nested dependencies

A provider's own parameters are classified and resolved the same way a resolver's are -- so a
provider can itself depend on another provider, to any depth:

```python
def get_settings() -> Settings:
    return load_settings()

async def get_gel_client(
    info: bramble.Info,
    settings: Annotated[Settings, bramble.Depends(get_settings)],
) -> AsyncIterator[GelClient]:
    client = await create_gel_client(settings.dsn)
    try:
        yield client
    finally:
        await client.aclose()
```

A provider's own parameters may only be `Info` and/or `Depends[T]` -- there's no other source for
them to come from (a provider isn't tied to a particular field's own query-supplied arguments or
parent value).

## Caching scope

A dependency is resolved **once per scope**, then reused:

- **Query/mutation**: one cache, scoped to the single request.
- **Subscription**: one cache, scoped to the *subscription's own lifetime* -- created once
  (covering the root resolver call that creates the event stream), reused across every event it
  emits, torn down exactly once when the subscription ends (normal completion, the client
  unsubscribing, an error, or the connection dropping -- all four trigger cleanup exactly once).
  Never per-connection (two concurrent subscriptions on the same connection never share an
  instance) and never per-emitted-event (a dependency isn't re-resolved for every single message).

The cache key is the provider callable's own identity -- two different injection sites using the
same provider function share one resolved instance within a scope, however deep in the dependency
graph each one is:

```python
@bramble.type
class Query:
    @bramble.field
    async def a(client: Annotated[GelClient, bramble.Depends(get_gel_client)]) -> str:
        ...

    @bramble.field
    async def b(client: Annotated[GelClient, bramble.Depends(get_gel_client)]) -> str:
        ...  # same client instance as `a`, within one request
```

**Single-flight**: GraphQL execution frequently resolves sibling fields concurrently. If two
resolvers need the same not-yet-cached dependency at roughly the same time, the provider still
only runs once -- the second caller awaits the first's already-in-progress call rather than
triggering a second one.

**Generator-provider teardown** runs exactly once per cache entry, at the end of its scope -- not
once per injection site, even if the same provider is used in several places within one scope.

### `use_cache=False`

Opts one specific `Depends(...)` call out of both cache reads and writes -- the provider still
goes through the same resolution machinery (nested dependencies, single-flight), it just never
joins or populates the shared cache, so it gets its own fresh instance every time:

```python
@bramble.field
def value(x: Annotated[str, bramble.Depends(get_request_id, use_cache=False)]) -> str:
    ...
```

## Pre-seeding with `resolved_dependencies`

Skip a provider entirely by seeding its value directly into the cache before execution starts,
keyed by the provider callable itself:

```python
result = await schema.execute_async(
    query,
    context=context,
    resolved_dependencies={get_gel_client: existing_client},
)
```

Available on `Schema.execute`/`execute_async`/`execute_incremental`/`subscribe_async`. A seeded
value's provider is never called -- and bramble never runs its teardown either, since bramble
never owned it to begin with (whatever created it upstream remains responsible for cleanup).

## Scope limits

- A provider function's own signature is only ever classified lazily, the first time a dependency
  chain actually reaches it at request execution time -- not eagerly when `Schema()` is built,
  unlike every other schema-shape validation in bramble. Providers aren't part of the GraphQL type
  graph at all (no SDL representation, nothing to validate ahead of time), so a mistake in one
  (an unresolvable annotation, an unsupported parameter kind) only surfaces the first time a query
  actually exercises it.

## How a provider is identified

bramble identifies a provider by the callable object itself -- that is the key
for both the per-request cache and `resolved_dependencies=` seeding. Two
`Depends(...)` sites referring to the same function share one cached value;
two different functions never collide, even if one is garbage-collected and
another later occupies its address.

Module-level functions are the ordinary case and need no thought. A provider
built per request (a closure, a `functools.partial`, a bound method) also
works, with one consequence worth knowing: it is a *new object* each request,
so it is a distinct provider each time. Caching within a request still
applies; there is nothing to share across requests, and
`resolved_dependencies={the_old_object: ...}` will not match it.

If you need per-request information, prefer reading it from `Info` in a
module-level provider over building a provider per request:

```python
async def get_client(info: bramble.Info) -> AsyncIterator[Client]:
    client = await connect(info.context["dsn"])
    try:
        yield client
    finally:
        await client.aclose()
```
