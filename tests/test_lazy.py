from __future__ import annotations

from typing import Annotated, ForwardRef

import bramble
from bramble._lazy import LazyReference, LazyType
from bramble.directive import DirectiveLocation, DirectiveValue
from lazy_fixtures.authors import Author, AuthorSearch
from lazy_fixtures.posts import Post


def test_lazy_returns_a_lazy_reference() -> None:
    reference = bramble.lazy("some.module")
    assert isinstance(reference, LazyReference)
    assert reference.module == "some.module"
    assert reference.package is None


def test_lazy_reference_resolves_an_absolute_forward_ref() -> None:
    reference = bramble.lazy("lazy_fixtures.posts")
    lazy_type = reference.resolve_forward_ref(ForwardRef("Post"))

    assert lazy_type == LazyType("Post", "lazy_fixtures.posts", None)
    assert lazy_type.resolve_type() is Post


def test_circular_field_references_resolve_through_schema_and_execute() -> None:
    author = Author(name="Ada", posts=[])
    post = Post(title="Hello GraphQL", author=author)
    author.posts.append(post)

    @bramble.type
    class Query:
        @bramble.field
        def author() -> Author:
            return author

    schema = bramble.Schema(query=Query, types=[Author, Post])

    result = schema.execute("{ author { name posts { title author { name } } } }")
    assert result == {
        "data": {
            "author": {
                "name": "Ada",
                "posts": [{"title": "Hello GraphQL", "author": {"name": "Ada"}}],
            }
        }
    }


def test_circular_field_references_render_the_real_types_in_sdl() -> None:
    @bramble.type
    class Query:
        author: Author

    schema = bramble.Schema(query=Query, types=[Author, Post])
    sdl = schema.to_sdl()

    assert "posts: [Post!]!" in sdl
    assert "author: Author!" in sdl
    assert "LazyType" not in sdl


def test_resolver_argument_typed_via_lazy_reference() -> None:
    author = Author(name="Ada", posts=[])

    @bramble.type
    class Query:
        @bramble.field
        def search(criteria: Annotated["AuthorSearch", bramble.lazy("lazy_fixtures.authors")]) -> Author:
            return author

    schema = bramble.Schema(query=Query, types=[Author, Post])

    assert "search(criteria: AuthorSearch!): Author!" in schema.to_sdl()
    result = schema.execute('{ search(criteria: {name: "Ada"}) { name } }')
    assert result == {"data": {"search": {"name": "Ada"}}}


def test_directive_argument_typed_via_lazy_reference() -> None:
    @bramble.directive(locations=[DirectiveLocation.FIELD])
    def echo(
        value: DirectiveValue[str], tag: Annotated["AuthorSearch", bramble.lazy("lazy_fixtures.authors")]
    ) -> str:
        return f"{value}-{tag.name}"

    @bramble.type
    class Query:
        @bramble.field
        def greeting() -> str:
            return "hi"

    schema = bramble.Schema(query=Query, directives=[echo], types=[Author, Post])

    result = schema.execute('{ greeting @echo(tag: {name: "tagged"}) }')
    assert result == {"data": {"greeting": "hi-tagged"}}
