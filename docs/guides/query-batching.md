# Query batching

Every bramble HTTP integration can accept a JSON array of operations in a
single POST request, executing each and returning a JSON array of
responses in the same order -- disabled by default (a client that doesn't
need it never pays for the extra request-shape branching), enabled via
[`SchemaConfig.batching_config`](../types/schema-configurations.md#batching_config):

```python
from bramble.schema.config import SchemaConfig

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
```

```json
[{"data": {"greet": "Hello, A!"}}, {"data": {"greet": "Hello, B!"}}]
```

Each operation in the batch is executed independently and concurrently
(via `asyncio.gather`), in the order given. `max_operations` caps how many
operations a single batch request may contain -- exceeding it, or sending
a batch request at all when `batching_config` is unset, is rejected with
an HTTP 400.
