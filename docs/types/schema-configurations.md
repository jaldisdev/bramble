# Schema configurations

`bramble.schema.config.SchemaConfig` controls schema-wide behavior, passed
as `Schema(config=...)`:

```python
from bramble.schema.config import SchemaConfig

schema = bramble.Schema(
    query=Query,
    config=SchemaConfig(
        auto_camel_case=True,
        scalar_map={...},
        batching_config={"max_operations": 5},
    ),
)
```

## `auto_camel_case`

Default `True`. When enabled, a field/argument with no explicit `name=`
override is exposed under a camelCase rendering of its Python identifier
(`get_user` -> `getUser`). Set to `False` to keep raw Python identifiers as
the GraphQL-facing names instead -- an explicit `name=` override still
takes precedence either way. See
[Schema basics](../general/schema-basics.md#naming-camelcase-by-default).

## `scalar_map`

Registers [custom scalars](scalars.md): a `dict` from the Python type to a
`bramble.scalar(...)` definition describing its name, description, and
(de)serialization. See [Scalars](scalars.md#custom-scalars).

## `batching_config`

`None` by default (disabled). Set `{"max_operations": N}` to let bramble's
HTTP integrations accept a JSON array of up to `N` operations in a single
POST request, executing each and returning a JSON array of responses in the
same order:

```python
schema = bramble.Schema(query=Query, config=SchemaConfig(batching_config={"max_operations": 5}))
```

```python
response = client.post(
    "/graphql",
    json=[
        {"query": 'query { greet(name: "A") }'},
        {"query": 'query { greet(name: "B") }'},
    ],
)
# [{"data": {"greet": "Hello, A!"}}, {"data": {"greet": "Hello, B!"}}]
```

A batch request exceeding `max_operations`, or any batch request at all
when `batching_config` is unset, is rejected with an HTTP 400. See
[Query batching](../guides/query-batching.md).

## `validate_queries`

Default `True`: every incoming query is validated against the schema before
it executes. **Leave it that way.** Validation is what turns a malformed or
schema-violating query into one clear error before any resolver runs.

`validate_queries=False` is a transitional escape hatch, not a performance
knob. It exists for one situation: porting a schema that ran unvalidated
somewhere else (Strawberry's `DisableValidation`, say), where turning
validation on at the same moment as the port would mean two behavior
changes landing together with no way to tell which one broke a client.

```python
# Temporary. Turn this back on as its own change, with its own rollout.
schema = bramble.Schema(query=Query, config=SchemaConfig(validate_queries=False))
```

With it off, an invalid query does not become a valid one -- it fails
later, deeper, and less clearly, as whatever the executor raises when it
reaches an unknown field, a missing required argument, or an argument of
the wrong type. The switch covers `execute_async`, `execute_incremental`,
`subscribe_async`, and registering an Automatic Persisted Query.

`Schema.validate_query(query)` is deliberately unaffected: calling it *is*
the request to validate. That makes it the tool for the way back -- replay
your captured traffic through it, fix what it reports, then drop the
config flag.
