# Mutations

A `mutation` root works exactly like the `query` root -- an
`@bramble.type`-decorated class whose fields are resolvers -- passed to
`Schema(mutation=...)`:

```python
import bramble

@bramble.type
class Comment:
    @bramble.field
    def id(parent: bramble.Parent["CommentRecord"]) -> bramble.ID:
        return parent.id

    @bramble.field
    def body(parent: bramble.Parent["CommentRecord"]) -> str:
        return parent.body

@bramble.type
class Mutation:
    @bramble.field
    def add_comment(post_id: bramble.ID, body: str, info: bramble.Info) -> Comment:
        database = info.context
        if post_id not in database.posts:
            raise bramble.GraphQLError(f"no such post '{post_id}'", code=bramble.ErrorCode.ARGUMENT_TYPE_MISMATCH)
        return database.add_comment(post_id=post_id, body=body)

schema = bramble.Schema(query=Query, mutation=Mutation)
```

```python
result = schema.execute(
    'mutation { addComment(postId: "p1", body: "Nice post!") { id body } }',
    context=database,
)
# {'data': {'addComment': {'id': 'c1', 'body': 'Nice post!'}}}
```

`bramble.mutation` is available as an alias for `bramble.field` -- it behaves
identically (both just build a resolver-backed `Field`); use whichever reads
better for a given root type.

## Root fields run serially

Per the GraphQL spec, a query's top-level fields (and any nested list items/
sibling fields) may resolve concurrently, but a **mutation's own root
fields** always execute one after another, in the order they appear in the
document -- so a mutation document with several top-level mutation fields
never has two of them touching shared state at the same time.

## Input types for structured arguments

A mutation that takes more than a couple of scalar arguments usually reads
better accepting one input object instead of several loose parameters -- see
[Input types](../types/input-types.md).
