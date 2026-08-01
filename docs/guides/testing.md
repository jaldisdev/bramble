# Testing

A bramble schema needs nothing beyond itself to test -- there's no test
client or app server required to exercise resolvers, since `Schema.execute`/
`execute_async` run entirely in-process:

```python
import bramble

def test_greet_returns_a_greeting() -> None:
    schema = bramble.Schema(query=Query)

    result = schema.execute('{ greet(name: "Ada") }')

    assert result == {"data": {"greet": "Hello, Ada!"}}
```

Pass `context=` to inject a fake/in-memory version of whatever a resolver
reads from `Info.context` -- a fake database, an in-memory repository, and
so on -- so a test never has to reach a real external dependency:

```python
def test_mutation_adds_a_comment() -> None:
    schema = build_schema()
    db = Database()  # a fresh, in-memory instance

    result = schema.execute(
        'mutation { addComment(postId: "p1", body: "Nice post!") { id body } }',
        context=db,
    )

    assert result == {"data": {"addComment": {"id": "c1", "body": "Nice post!"}}}
    assert db.comments["c1"].body == "Nice post!"
```

## Async resolvers, subscriptions, and incremental delivery

Use `execute_async` (via `asyncio.run` or `pytest-asyncio`) whenever a
query's path includes a coroutine resolver; `subscribe_async` and
`execute_incremental` are both async generators -- collect their output
with `async for`:

```python
import asyncio

async def _collect(generator):
    return [response async for response in generator]

def test_subscription_yields_one_response_per_event() -> None:
    schema = bramble.Schema(query=Query, subscription=Subscription)

    responses = asyncio.run(_collect(schema.subscribe_async("subscription { count(upto: 3) }")))

    assert responses == [{"data": {"count": 0}}, {"data": {"count": 1}}, {"data": {"count": 2}}]
```

## Validating a query without executing it

`Schema.validate_query` checks a query document against the schema without
running any resolver -- useful for a CI check over a directory of committed
`.graphql` files, or asserting that an intentionally-malformed query is
rejected:

```python
def test_unknown_field_is_rejected() -> None:
    schema = bramble.Schema(query=Query)

    with pytest.raises(bramble.GraphQLError) as excinfo:
        schema.validate_query("{ doesNotExist }")

    assert excinfo.value.code is bramble.ErrorCode.UNKNOWN_FIELD
```

## Testing an HTTP integration

Every bramble adapter is a real ASGI application (or, for Flask, a WSGI
one) usable directly with its own framework's test client -- see
[Integrations](../integrations/index.md) for the per-framework setup, and
`tests/test_adapters_*.py` in bramble's own repository for complete,
working examples against every integration, including WebSocket
subscriptions and `@defer`/`@stream` multipart responses.
