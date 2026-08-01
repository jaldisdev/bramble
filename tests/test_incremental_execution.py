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

import asyncio

import pytest

import bramble
from bramble._error import ErrorCode, GraphQLError
from bramble._execution import execute_async, execute_incremental

# `execute_incremental` targets the simpler `path`/`data`/`hasNext` payload shape (not the newer
# `pending`/`id`/`completed` tracking revision) -- see bramble/_execution.py's own "@defer/@stream
# incremental delivery" section header for the full scope this implements.


def _collect(schema: bramble.Schema, query: str, **kwargs: object) -> list[dict]:
    async def run() -> list[dict]:
        return [payload async for payload in execute_incremental(schema, query, **kwargs)]

    return asyncio.run(run())


@bramble.type
class Profile:
    @bramble.field
    def bio() -> str:
        return "hello"

    @bramble.field
    def broken() -> str:
        raise ValueError("boom")


@bramble.type
class Author:
    name: str

    @bramble.field
    def profile() -> Profile:
        return Profile()


@bramble.type
class Query:
    @bramble.field
    def id() -> str:
        return "q1"

    @bramble.field
    def author() -> Author:
        return Author(name="Ada")

    @bramble.field
    async def items() -> bramble.Streamable[int]:
        for i in range(5):
            yield i

    @bramble.field
    async def failing_items() -> bramble.Streamable[int]:
        yield 1
        raise ValueError("stream broke")


@bramble.type
class Result:
    ok: bool = True


@bramble.type
class Mutation:
    @bramble.field
    def do_thing() -> Result:
        return Result()


schema = bramble.Schema(query=Query, mutation=Mutation, types=[Author, Profile, Result])


def test_no_markers_degenerates_to_a_single_final_payload() -> None:
    payloads = _collect(schema, "query { id }")
    assert payloads == [{"data": {"id": "q1"}, "hasNext": False}]


def test_deferred_fragment_delivered_as_a_second_patch() -> None:
    payloads = _collect(
        schema,
        'query { id ... @defer(label: "extra") { author { name } } }',
    )
    assert payloads[0] == {"data": {"id": "q1"}, "hasNext": True}
    assert payloads[1] == {
        "incremental": [{"data": {"author": {"name": "Ada"}}, "path": [], "label": "extra"}],
        "hasNext": False,
    }
    assert len(payloads) == 2


def test_deferred_fragment_without_a_label_omits_the_label_key() -> None:
    payloads = _collect(schema, "query { id ... @defer { author { name } } }")
    assert "label" not in payloads[1]["incremental"][0]


def test_streamed_field_delivers_initial_count_then_per_item_patches() -> None:
    payloads = _collect(schema, "query { items @stream(initialCount: 2) }")
    assert payloads[0] == {"data": {"items": [0, 1]}, "hasNext": True}
    assert payloads[1] == {"incremental": [{"items": [2], "path": ["items"]}], "hasNext": True}
    assert payloads[2] == {"incremental": [{"items": [3], "path": ["items"]}], "hasNext": True}
    assert payloads[3] == {"incremental": [{"items": [4], "path": ["items"]}], "hasNext": False}
    assert len(payloads) == 4


def test_streamed_field_with_initial_count_covering_the_whole_list_spawns_no_job() -> None:
    payloads = _collect(schema, "query { items @stream(initialCount: 10) }")
    assert payloads == [{"data": {"items": [0, 1, 2, 3, 4]}, "hasNext": False}]


def test_combined_defer_and_stream_in_one_query() -> None:
    payloads = _collect(
        schema,
        "query { id items @stream(initialCount: 1) ... @defer { author { name } } }",
    )
    assert payloads[0] == {"data": {"id": "q1", "items": [0]}, "hasNext": True}
    # Both a stream job and a defer job are in flight -- order between them isn't guaranteed, but
    # both must appear, and only the truly last patch overall may claim `hasNext: false`.
    assert len(payloads) == 6
    for payload in payloads[:-1]:
        assert payload["hasNext"] is True
    assert payloads[-1]["hasNext"] is False
    stream_patches = [p for p in payloads[1:] if p["incremental"][0].get("path") == ["items"]]
    defer_patches = [p for p in payloads[1:] if p["incremental"][0].get("path") == []]
    assert [entry["incremental"][0]["items"] for entry in stream_patches] == [[1], [2], [3], [4]]
    assert defer_patches[0]["incremental"][0]["data"] == {"author": {"name": "Ada"}}


def test_field_colliding_with_a_non_deferred_sibling_resolves_eagerly() -> None:
    """The defer-exclusivity scope limit: a field selected both inside and outside a deferred
    fragment is needed for the initial payload anyway, so it's resolved immediately rather than
    deferred -- documented behavior, not a bug.
    """
    payloads = _collect(schema, "query { author { name } ... @defer { author { name } } }")
    assert payloads == [{"data": {"author": {"name": "Ada"}}, "hasNext": False}]


def test_nested_defer_inside_a_deferred_fragments_own_subtree() -> None:
    payloads = _collect(
        schema,
        """
        query {
            id
            ... @defer(label: "outer") {
                author {
                    name
                    ... @defer(label: "inner") {
                        profile { bio }
                    }
                }
            }
        }
        """,
    )
    assert payloads[0] == {"data": {"id": "q1"}, "hasNext": True}
    outer = next(p for p in payloads[1:] if p["incremental"][0]["label"] == "outer")
    inner = next(p for p in payloads[1:] if p["incremental"][0]["label"] == "inner")
    assert outer["incremental"][0] == {"data": {"author": {"name": "Ada"}}, "path": [], "label": "outer"}
    assert inner["incremental"][0] == {"data": {"profile": {"bio": "hello"}}, "path": ["author"], "label": "inner"}
    assert payloads[-1]["hasNext"] is False


def test_error_inside_a_deferred_field_becomes_that_patchs_own_error() -> None:
    payloads = _collect(schema, "query { id ... @defer { author { profile { broken } } } }")
    assert payloads[0] == {"data": {"id": "q1"}, "hasNext": True}
    patch = payloads[1]["incremental"][0]
    assert patch["data"] is None
    assert patch["errors"][0]["message"] == "boom"
    assert patch["errors"][0]["path"] == ["author", "profile", "broken"]


def test_error_fetching_the_next_stream_item_ends_that_stream_without_hanging() -> None:
    payloads = _collect(schema, "query { failingItems @stream(initialCount: 0) }")
    assert payloads[0] == {"data": {"failingItems": []}, "hasNext": True}
    item_patch = payloads[1]["incremental"][0]
    assert item_patch == {"items": [1], "path": ["failingItems"]}
    error_patch = payloads[2]["incremental"][0]
    assert error_patch["items"] == []
    assert "stream broke" in error_patch["errors"][0]["message"]
    assert payloads[2]["hasNext"] is False


def test_stream_on_a_non_async_generator_resolver_raises_immediately() -> None:
    @bramble.type
    class BadQuery:
        @bramble.field
        def items() -> bramble.Streamable[int]:  # not an async generator -- a plain sync method
            return None  # type: ignore[return-value]

    bad_schema = bramble.Schema(query=BadQuery)

    async def run() -> None:
        async for _ in execute_incremental(bad_schema, "query { items @stream }"):
            pass

    with pytest.raises(GraphQLError) as excinfo:
        asyncio.run(run())
    assert excinfo.value.code is ErrorCode.FIELD_RESOLUTION_FAILED


def test_mutation_with_defer_runs_serially_and_still_delivers_the_deferred_patch() -> None:
    payloads = _collect(schema, "mutation { doThing { ok } ... @defer { __typename } }")
    assert payloads[0]["data"]["doThing"] == {"ok": True}
    assert payloads[0]["hasNext"] is True
    assert payloads[1]["incremental"][0]["data"] == {"__typename": "Mutation"}
    assert payloads[1]["hasNext"] is False


def test_execute_async_rejects_a_query_using_defer() -> None:
    async def run() -> None:
        await execute_async(schema, "query { ... @defer { author { name } } }")

    with pytest.raises(GraphQLError) as excinfo:
        asyncio.run(run())
    assert "execute_incremental" in excinfo.value.message


def test_execute_async_rejects_a_query_using_stream() -> None:
    async def run() -> None:
        await execute_async(schema, "query { items @stream }")

    with pytest.raises(GraphQLError) as excinfo:
        asyncio.run(run())
    assert "execute_incremental" in excinfo.value.message


# --- Schema.execute_incremental (thin wrapper) ----------------------------------------------------


def test_schema_execute_incremental_delegates_correctly() -> None:
    async def run() -> list[dict]:
        return [
            payload
            async for payload in schema.execute_incremental(
                'query { id ... @defer(label: "extra") { author { name } } }'
            )
        ]

    payloads = asyncio.run(run())
    assert payloads[0] == {"data": {"id": "q1"}, "hasNext": True}
    assert payloads[1]["incremental"][0]["label"] == "extra"
    assert payloads[1]["hasNext"] is False
