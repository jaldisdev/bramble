#
# This source file is part of the Bramble open source project.
#
# Copyright (c) 2026 Jaldis B.V.
#
# Licensed under the MIT OR Apache-2.0 license (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://opensource.org/licenses/MIT
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, ForwardRef

from lazy_fixtures.authors import Author, AuthorSearch
from lazy_fixtures.posts import Post

import bramble
from bramble._lazy import LazyReference, LazyType
from bramble.directive import DirectiveLocation, DirectiveValue
from tests.lazy_fixtures.comments import Comment

if TYPE_CHECKING:
    from tests.lazy_fixtures.renamed import Thing


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


def test_a_lazy_reference_may_be_optional() -> None:
    """`SomeLazyType | None` is an ordinary thing to write, and it used to raise `TypeError:
    unsupported operand type(s) for |` -- `|` only works between real types, and a lazy reference
    is deliberately not one yet. Every other lazy test used the non-optional form, so nothing
    caught it.
    """
    sdl = bramble.Schema(query=_OptionalLazyQuery).to_sdl()
    assert "author: Author" in sdl
    assert "type Author" in sdl


@bramble.type
class _OptionalLazyQuery:
    @bramble.field
    def comment() -> Comment:
        raise NotImplementedError


def test_a_lazy_reference_to_a_renamed_type_uses_its_graphql_name() -> None:
    """A lazily-referenced type that renamed itself with `@bramble.type(name=...)` must be
    *referenced* by that name too. The placeholder carries the Python name written in the forward
    reference, and using it unconditionally emitted SDL declaring `type RenamedThing` while every
    field said `Thing!` -- inconsistent, and referring to a type that does not exist.
    """
    sdl = bramble.Schema(query=_RenamedLazyQuery).to_sdl()

    assert "type RenamedThing" in sdl
    assert "thing: RenamedThing!" in sdl
    assert "Renamed" in sdl and ": Thing!" not in sdl


@bramble.type
class _RenamedLazyQuery:
    @bramble.field
    def thing() -> Annotated["Thing", bramble.lazy("tests.lazy_fixtures.renamed")]:
        raise NotImplementedError


def test_a_lazy_annotated_resolver_executes() -> None:
    """The schema building is not enough: a resolver's parameters are classified again at *request*
    time (to find `Depends` markers, which the Rust IR does not carry), and that classifier has to
    seed the same lazy placeholders. Without them a resolver annotated
    `-> Annotated["Other", bramble.lazy(...)]` raised `NameError` as a *field* error on exactly the
    fields that use `bramble.lazy`, while the schema itself built perfectly.
    """
    schema = bramble.Schema(query=_LazyExecutionQuery)

    result = schema.execute("{ comment { body } }")

    assert result.get("errors") is None
    assert result["data"] == {"comment": {"body": "hello"}}


@bramble.type
class _LazyExecutionQuery:
    @bramble.field
    def comment() -> Comment:
        return Comment(body="hello", author=None)
