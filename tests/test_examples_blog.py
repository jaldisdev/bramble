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

"""Exercises the `examples/blog` schema end to end, mirroring the acceptance criteria from
Tasks 3-11 of the implementation plan against one coherent example rather than isolated
fixtures -- see `examples/blog/schema.py` for the schema itself.
"""

from __future__ import annotations

import asyncio
import hashlib

import pytest

import bramble
from examples.blog.schema import Database, build_schema


@pytest.fixture
def schema() -> bramble.Schema:
    return build_schema()


@pytest.fixture
def db() -> Database:
    return Database()


# --- Task 8b: Schema() registration --------------------------------------------------------------


def test_schema_registers_every_reachable_type(schema: bramble.Schema) -> None:
    assert {"Query", "Mutation", "Node", "Author", "Post", "Comment"} <= set(schema.types_by_name)


def test_schema_registers_the_interface_implementors(schema: bramble.Schema) -> None:
    implementor_names = {cls.__name__ for cls in schema.implementors_by_interface["Node"]}
    assert implementor_names == {"Author", "Post"}


def test_schema_registers_the_union(schema: bramble.Schema) -> None:
    assert "SearchResult" in schema.unions_by_name
    member_names = {cls.__name__ for cls in schema.union_members_by_name["SearchResult"]}
    assert member_names == {"Author", "Post"}


def test_schema_registers_the_schema_directive(schema: bramble.Schema) -> None:
    assert "auth" in schema.schema_directives_by_name
    assert schema.schema_directives_by_name["auth"].locations == ["FIELD_DEFINITION"]


# --- Task 3/3b/4: types, custom scalars, Parent/Info injection --------------------------------


def test_nested_object_field_uses_parent_and_info_injection(schema: bramble.Schema, db: Database) -> None:
    result = schema.execute("query { posts { title author { name } } }", context=db)

    assert result == {"data": {"posts": [{"title": "Hello GraphQL", "author": {"name": "Ada Lovelace"}}]}}


def test_custom_scalar_serializes_on_output(schema: bramble.Schema, db: Database) -> None:
    result = schema.execute("query { posts { slug } }", context=db)

    assert result == {"data": {"posts": [{"slug": "hello-graphql"}]}}


def test_custom_scalar_parses_on_input(schema: bramble.Schema, db: Database) -> None:
    result = schema.execute('query { postBySlug(slug: "Hello GraphQL") { title } }', context=db)

    assert result == {"data": {"postBySlug": {"title": "Hello GraphQL"}}}


def test_builtin_datetime_scalar_serializes_to_isoformat(schema: bramble.Schema, db: Database) -> None:
    result = schema.execute("query { posts { publishedAt } }", context=db)

    assert result == {"data": {"posts": [{"publishedAt": "2024-01-01T09:00:00"}]}}


# --- Task 5: is_type_of interface dispatch -------------------------------------------------------


def test_node_interface_dispatches_to_post_via_is_type_of(schema: bramble.Schema, db: Database) -> None:
    result = schema.execute('query { node(id: "p1") { __typename id ... on Post { title } } }', context=db)

    assert result == {"data": {"node": {"__typename": "Post", "id": "p1", "title": "Hello GraphQL"}}}


def test_node_interface_dispatches_to_author_via_is_type_of(schema: bramble.Schema, db: Database) -> None:
    result = schema.execute('query { node(id: "a1") { __typename id ... on Author { name } } }', context=db)

    assert result == {"data": {"node": {"__typename": "Author", "id": "a1", "name": "Ada Lovelace"}}}


def test_node_lookup_miss_returns_null(schema: bramble.Schema, db: Database) -> None:
    result = schema.execute('query { node(id: "missing") { __typename } }', context=db)

    assert result == {"data": {"node": None}}


# --- Task 6: union + custom resolve_type ---------------------------------------------------------


def test_union_search_dispatches_both_member_types(schema: bramble.Schema, db: Database) -> None:
    result = schema.execute(
        """
        query {
            search(term: "a") {
                __typename
                ... on Author { name }
                ... on Post { title }
            }
        }
        """,
        context=db,
    )

    typenames = {item["__typename"] for item in result["data"]["search"]}
    assert typenames == {"Author", "Post"}


# --- Task 7: schema directive rendered into SDL, no runtime behavior of its own ----------------


def test_schema_directive_is_declarative_only_field_still_resolves(schema: bramble.Schema, db: Database) -> None:
    """§6: a schema directive carries no runtime behavior -- `internalNotes` still resolves
    normally even though it's marked `@auth(role: "admin")`; enforcing that role is a field
    extension's job, not the directive's.
    """
    result = schema.execute("query { posts { internalNotes } }", context=db)

    assert result == {"data": {"posts": [{"internalNotes": "flagged for editorial review"}]}}


def test_schema_directive_appears_in_sdl(schema: bramble.Schema) -> None:
    sdl = schema.to_sdl()

    assert 'internalNotes: String! @auth(role: "admin")' in sdl
    assert "directive @auth(role: String!) on FIELD_DEFINITION" in sdl


# --- Task 8: custom operation directive -----------------------------------------------------------


def test_custom_operation_directive_transforms_resolved_value(schema: bramble.Schema, db: Database) -> None:
    result = schema.execute("query { posts { title @shout } }", context=db)

    assert result == {"data": {"posts": [{"title": "HELLO GRAPHQL"}]}}


# --- Task 9: query validation ---------------------------------------------------------------------


def test_validate_query_accepts_a_well_formed_query(schema: bramble.Schema) -> None:
    schema.validate_query("query { posts { title } }")


def test_validate_query_rejects_an_unknown_field(schema: bramble.Schema) -> None:
    with pytest.raises(bramble.GraphQLError) as excinfo:
        schema.validate_query("query { posts { doesNotExist } }")
    assert excinfo.value.code is bramble.ErrorCode.UNKNOWN_FIELD


# --- Task 10: persisted-query caching -------------------------------------------------------------


def test_persisted_query_round_trip(schema: bramble.Schema) -> None:
    query_text = "query { posts { title } }"
    sha256_hash = hashlib.sha256(query_text.encode("utf-8")).hexdigest()

    was_cache_hit = schema.resolve_persisted_query(sha256_hash, query=query_text)
    assert was_cache_hit is False

    was_cache_hit_again = schema.resolve_persisted_query(sha256_hash)
    assert was_cache_hit_again is True


# --- Task 11: execution bridge (interface + union + custom directive + async, all at once) ------


def test_execute_async_directly_with_async_resolver(schema: bramble.Schema, db: Database) -> None:
    result = asyncio.run(schema.execute_async('query { search(term: "ada") { __typename } }', context=db))

    assert result == {"data": {"search": [{"__typename": "Author"}]}}


def test_mutation_adds_a_comment_and_resolves_its_author(schema: bramble.Schema, db: Database) -> None:
    result = schema.execute(
        'mutation { addComment(postId: "p1", body: "Nice post!") { id body author { name } } }', context=db
    )

    assert result == {
        "data": {"addComment": {"id": "c1", "body": "Nice post!", "author": {"name": "Ada Lovelace"}}}
    }
    assert db.comments["c1"].body == "Nice post!"


def test_mutation_against_unknown_post_is_a_field_error(schema: bramble.Schema, db: Database) -> None:
    result = schema.execute('mutation { addComment(postId: "missing", body: "x") { id } }', context=db)

    assert result["data"] is None
    assert result["errors"][0]["message"] == "no such post 'missing'"


def test_full_query_combining_interface_union_custom_scalar_and_directive(
    schema: bramble.Schema, db: Database
) -> None:
    result = schema.execute(
        """
        query {
            node(id: "p1") {
                __typename
                ... on Post {
                    title @shout
                    slug
                    excerpt(length: 5)
                }
            }
        }
        """,
        context=db,
    )

    assert result == {
        "data": {
            "node": {
                "__typename": "Post",
                "title": "HELLO GRAPHQL",
                "slug": "hello-graphql",
                "excerpt": "This ",
            }
        }
    }
