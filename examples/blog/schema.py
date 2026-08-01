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

"""A small blog schema exercising every mechanism from the implementation plan's Tasks 3-11 in
one coherent place: interfaces with `is_type_of` (§4), a union with a custom `resolve_type` (§5),
a schema directive (§6), a custom operation directive (§7), a custom scalar (§3b), `Parent`/`Info`
resolver injection (§4), an async resolver, and a mutation -- all wired up through `Schema()`
(§7b) and runnable via `Schema.execute`/`execute_async` (§11).

This file is meant to double as documentation: it has no test assertions of its own (see
`tests/test_examples_blog.py` for those), just a schema a reader can follow end to end.
"""

from __future__ import annotations

import dataclasses
import datetime
from typing import Annotated, NewType, Union

import bramble
from bramble.directive import DirectiveLocation, DirectiveValue
from bramble.schema.config import SchemaConfig
from bramble.schema_directive import Location

# --- Domain layer -------------------------------------------------------------------------------
# Plain Python objects with no GraphQL awareness -- resolvers read from these via `Parent[T]`;
# nothing here is ever wrapped in an instance of a `@bramble.type`-decorated class.


@dataclasses.dataclass
class AuthorRecord:
    id: str
    name: str


@dataclasses.dataclass
class PostRecord:
    id: str
    title: str
    body: str
    author_id: str
    published_at: datetime.datetime


@dataclasses.dataclass
class CommentRecord:
    id: str
    post_id: str
    author_id: str
    body: str


class Database:
    """Stands in for a real datastore -- passed as `Schema.execute(..., context=db)`, reachable
    from any resolver via `Info.context`.
    """

    def __init__(self) -> None:
        self.authors: dict[str, AuthorRecord] = {"a1": AuthorRecord(id="a1", name="Ada Lovelace")}
        self.posts: dict[str, PostRecord] = {
            "p1": PostRecord(
                id="p1",
                title="Hello GraphQL",
                body="This is the first post on the blog.",
                author_id="a1",
                published_at=datetime.datetime(2024, 1, 1, 9, 0, 0),
            ),
        }
        self.comments: dict[str, CommentRecord] = {}
        self._next_comment_id = 1

    def add_comment(self, *, post_id: str, author_id: str, body: str) -> CommentRecord:
        comment_id = f"c{self._next_comment_id}"
        self._next_comment_id += 1
        comment = CommentRecord(id=comment_id, post_id=post_id, author_id=author_id, body=body)
        self.comments[comment_id] = comment
        return comment

    def post_by_slug(self, slug: str) -> PostRecord | None:
        for post in self.posts.values():
            if _slugify(post.title) == slug:
                return post
        return None


# --- Custom scalar: Slug (§3b) -------------------------------------------------------------------
# A validated, always-lowercase-hyphenated string. `Slug` stays a real `str` as far as type
# checkers/resolvers are concerned; the (de)serialization behavior is registered separately via
# `SchemaConfig.scalar_map`, not by wrapping the `NewType` itself.

Slug = NewType("Slug", str)


def _slugify(title: str) -> str:
    return title.lower().replace(" ", "-")


# --- Schema directive (§6) -----------------------------------------------------------------------
# Declarative-only metadata -- rendered into SDL, carries no runtime behavior of its own (an actual
# auth check would be a field extension; the directive just documents that one applies).


@bramble.schema_directive(locations=[Location.FIELD_DEFINITION])
class Auth:
    role: str


# --- Operation directive (§7) --------------------------------------------------------------------
# A per-field value transform, applied client-side via `@shout` in a query string.


@bramble.directive(locations=[DirectiveLocation.FIELD], description="Uppercases a resolved string value")
def shout(value: DirectiveValue[str]) -> str:
    return value.upper()


# --- Interface (§4) -------------------------------------------------------------------------------


@bramble.interface
class Node:
    @bramble.field
    def id(parent: bramble.Parent[object]) -> bramble.ID:
        return parent.id  # type: ignore[attr-defined]


@bramble.type
class Author(Node):
    @bramble.field
    def name(parent: bramble.Parent[AuthorRecord]) -> str:
        return parent.name

    @classmethod
    def is_type_of(cls, obj: object, info: bramble.Info) -> bool:
        return isinstance(obj, AuthorRecord)


@bramble.type
class Post(Node):
    @bramble.field
    def title(parent: bramble.Parent[PostRecord]) -> str:
        return parent.title

    @bramble.field
    def excerpt(parent: bramble.Parent[PostRecord], length: int = 40) -> str:
        return parent.body[:length]

    @bramble.field
    def slug(parent: bramble.Parent[PostRecord]) -> Slug:
        return _slugify(parent.title)

    @bramble.field
    def published_at(parent: bramble.Parent[PostRecord]) -> datetime.datetime:
        return parent.published_at

    @bramble.field
    def author(parent: bramble.Parent[PostRecord], info: bramble.Info) -> Author:
        database: Database = info.context
        return database.authors[parent.author_id]

    @bramble.field(directives=[Auth(role="admin")])
    def internal_notes(parent: bramble.Parent[PostRecord]) -> str:
        return "flagged for editorial review"

    @classmethod
    def is_type_of(cls, obj: object, info: bramble.Info) -> bool:
        return isinstance(obj, PostRecord)


@bramble.type
class Comment:
    @bramble.field
    def id(parent: bramble.Parent[CommentRecord]) -> bramble.ID:
        return parent.id

    @bramble.field
    def body(parent: bramble.Parent[CommentRecord]) -> str:
        return parent.body

    @bramble.field
    def author(parent: bramble.Parent[CommentRecord], info: bramble.Info) -> Author:
        database: Database = info.context
        return database.authors[parent.author_id]


# --- Union with a custom resolve_type (§5) --------------------------------------------------------


def _resolve_search_result_type(obj: object, info: object) -> type:
    if isinstance(obj, AuthorRecord):
        return Author
    if isinstance(obj, PostRecord):
        return Post
    raise bramble.GraphQLError(
        f"'{type(obj).__name__}' is not a search result type", code=bramble.ErrorCode.UNION_TYPE_RESOLUTION_FAILED
    )


SearchResult = Annotated[Union[Author, Post], bramble.union("SearchResult", resolve_type=_resolve_search_result_type)]


# --- Root types -------------------------------------------------------------------------------


@bramble.type
class Query:
    @bramble.field
    def node(id: bramble.ID, info: bramble.Info) -> Node | None:
        database: Database = info.context
        return database.authors.get(id) or database.posts.get(id)

    @bramble.field
    def posts(info: bramble.Info) -> list[Post]:
        database: Database = info.context
        return list(database.posts.values())

    @bramble.field
    def post_by_slug(slug: Slug, info: bramble.Info) -> Post | None:
        database: Database = info.context
        return database.post_by_slug(slug)

    @bramble.field
    async def search(term: str, info: bramble.Info) -> list[SearchResult]:
        """Async purely to demonstrate that bramble awaits coroutine resolvers correctly (§11) --
        this particular search doesn't actually need to be asynchronous.
        """
        database: Database = info.context
        term_lower = term.lower()
        results: list[object] = [author for author in database.authors.values() if term_lower in author.name.lower()]
        results += [post for post in database.posts.values() if term_lower in post.title.lower()]
        return results


@bramble.type
class Mutation:
    @bramble.field
    def add_comment(post_id: bramble.ID, body: str, info: bramble.Info) -> Comment:
        database: Database = info.context
        if post_id not in database.posts:
            raise bramble.GraphQLError(f"no such post '{post_id}'", code=bramble.ErrorCode.ARGUMENT_TYPE_MISMATCH)
        return database.add_comment(post_id=post_id, author_id="a1", body=body)


def build_schema() -> bramble.Schema:
    return bramble.Schema(
        query=Query,
        mutation=Mutation,
        types=[Author, Post, Comment],
        directives=[shout],
        config=SchemaConfig(
            scalar_map={
                Slug: bramble.scalar(name="Slug", serialize=lambda value: value, parse_value=_slugify),
            }
        ),
    )
