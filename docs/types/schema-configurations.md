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
