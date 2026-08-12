# Resolvers

A resolver is any callable behind an `@bramble.field`-decorated attribute --
a method-style function defined in the class body, or a plain function
assigned via `bramble.field(resolver=...)`. Either way, bramble inspects the
resolver's own parameter *annotations* to decide what each parameter
receives: there's no positional convention to remember.

## No implicit `self`

A resolver never receives an implicit `self`/parent as its first positional
parameter, even when written as a method inside the class body:

```python
@bramble.type
class Post:
    @bramble.field
    def title(parent: bramble.Parent["PostRecord"]) -> str:
        return parent.title
```

This reads like a method, but `parent` is an ordinary parameter picked up by
its `Parent[...]` annotation -- not an implicit first argument. A field that
doesn't need the parent value at all can omit it entirely:

```python
@bramble.type
class Query:
    @bramble.field
    def hello() -> str:
        return "hi"
```

## `Parent[T]`

Annotate a parameter `Parent[T]` to receive the value being resolved *from*
-- the object returned by the parent field's own resolver (or, for a root
type's fields, the `root_value` passed to `execute`/`execute_async`):

```python
@bramble.field
def author(parent: bramble.Parent["PostRecord"], info: bramble.Info) -> "Author":
    database = info.context
    return database.authors[parent.author_id]
```

`T` is purely for type-checker documentation -- bramble looks for the
`Parent[...]` annotation itself, not any particular `T`.

## `Info`

Annotate a parameter `Info` (optionally `Info[ContextType, RootValueType]`
for type-checker precision) to receive the current execution's context:

```python
class Info:
    field_name: str                       # this field's GraphQL name
    python_name: str                      # this field's Python attribute name
    context: ContextType                  # whatever was passed as `context=` to execute()
    root_value: RootValueType             # whatever was passed as `root_value=`
    variable_values: dict[str, Any]       # the operation's GraphQL variables
    query: str | None                     # the raw query source text
    path: Path                            # this field's position in the response
    selected_fields: list[SelectedField]  # this field's own child selection
    schema: Schema                        # the schema currently executing
```

`info.context` is the usual place to reach request-scoped state -- a
database connection, the authenticated user, and so on:

```python
@bramble.type
class Query:
    @bramble.field
    def posts(info: bramble.Info) -> list["Post"]:
        database = info.context
        return list(database.posts.values())

schema.execute("{ posts { title } }", context=my_database)
```

If `Schema(default_context_factory=make_context)` was set, `info.context`
defaults to a fresh `SomeClass()` instance whenever a caller doesn't pass
`context=` explicitly.

## `Depends[T]`

Annotate a parameter `Annotated[T, bramble.Depends(provider)]` to receive a
value produced by `provider` -- dependency injection, invisible to the
GraphQL schema exactly like `Parent[T]`/`Info`. See
[Dependency injection](dependency-injection.md) for the full reference
(provider shapes, caching scope, nested dependencies).

## Arguments

Every other annotated parameter becomes a GraphQL argument, named after the
parameter (camelCased by default, like fields) and typed from its
annotation -- see [Queries](../general/queries.md#arguments) for the basics,
and use `typing.Annotated[T, bramble.argument(...)]` to override an
argument's GraphQL name or attach a deprecation reason/directives
independently of the Python parameter name.

## Sync and async resolvers

A resolver can be a plain function or an `async def` -- bramble awaits a
coroutine resolver automatically. Mixing sync and async resolvers freely in
the same schema is fine; use `execute_async`/`subscribe_async` (rather than
the synchronous `execute`) whenever any resolver in a query's path is a
coroutine.

## Duck-typed parent values

A resolver's `Parent[T]` value is never required to be an instance of any
particular bramble-declared class -- `T` is just a type hint. Any object
with the right attributes (a dataclass, an ORM model instance, a plain
`SimpleNamespace`, ...) works, as long as each field's resolver knows how to
read from it. This is why `examples/blog/schema.py` keeps its `AuthorRecord`/
`PostRecord` domain dataclasses completely separate from the `Author`/`Post`
GraphQL types -- a resolver is free to bridge between whatever your storage
layer already returns and what a field needs to produce.
