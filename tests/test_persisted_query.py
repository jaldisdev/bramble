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

import hashlib

import pytest

import bramble
import bramble._execution


@bramble.type
class Query:
    @bramble.field
    def greet(name: str) -> str:
        return name


def _hash(query_text: str) -> str:
    return hashlib.sha256(query_text.encode("utf-8")).hexdigest()


def _schema() -> bramble.Schema:
    return bramble.Schema(query=Query)


def test_hash_only_miss_raises_persisted_query_not_found() -> None:
    schema = _schema()
    query_text = 'query { greet(name: "Ada") }'

    with pytest.raises(bramble.GraphQLError) as excinfo:
        schema.resolve_persisted_query(_hash(query_text))

    assert excinfo.value.code is bramble.ErrorCode.PERSISTED_QUERY_NOT_FOUND
    # Apollo Client's own APQ link matches on this exact string to trigger its retry.
    assert str(excinfo.value) == "PersistedQueryNotFound"


def test_resend_with_query_registers_and_returns_false() -> None:
    schema = _schema()
    query_text = 'query { greet(name: "Ada") }'
    sha256_hash = _hash(query_text)

    was_cache_hit = schema.resolve_persisted_query(sha256_hash, query=query_text)

    assert was_cache_hit is False


def test_subsequent_hash_only_request_hits_cache() -> None:
    schema = _schema()
    query_text = 'query { greet(name: "Ada") }'
    sha256_hash = _hash(query_text)

    schema.resolve_persisted_query(sha256_hash, query=query_text)
    was_cache_hit = schema.resolve_persisted_query(sha256_hash)

    assert was_cache_hit is True


def test_mismatched_hash_is_rejected() -> None:
    schema = _schema()
    query_text = 'query { greet(name: "Ada") }'

    with pytest.raises(bramble.GraphQLError) as excinfo:
        schema.resolve_persisted_query("0" * 64, query=query_text)

    assert excinfo.value.code is bramble.ErrorCode.PERSISTED_QUERY_MISMATCH


def test_invalid_query_is_rejected_even_when_registering() -> None:
    schema = _schema()
    bad_query = "query { doesNotExist }"

    with pytest.raises(bramble.GraphQLError) as excinfo:
        schema.resolve_persisted_query(_hash(bad_query), query=bad_query)

    assert excinfo.value.code is bramble.ErrorCode.UNKNOWN_FIELD


def test_invalid_query_is_not_cached() -> None:
    schema = _schema()
    bad_query = "query { doesNotExist }"
    sha256_hash = _hash(bad_query)

    with pytest.raises(bramble.GraphQLError):
        schema.resolve_persisted_query(sha256_hash, query=bad_query)

    # The failed registration must not have polluted the cache -- a later hash-only lookup
    # should still miss with PersistedQueryNotFound, not silently succeed.
    with pytest.raises(bramble.GraphQLError) as excinfo:
        schema.resolve_persisted_query(sha256_hash)
    assert excinfo.value.code is bramble.ErrorCode.PERSISTED_QUERY_NOT_FOUND


def test_each_new_schema_starts_with_an_empty_cache() -> None:
    query_text = 'query { greet(name: "Ada") }'
    sha256_hash = _hash(query_text)

    first_schema = _schema()
    first_schema.resolve_persisted_query(sha256_hash, query=query_text)
    assert first_schema.resolve_persisted_query(sha256_hash) is True

    second_schema = _schema()
    with pytest.raises(bramble.GraphQLError) as excinfo:
        second_schema.resolve_persisted_query(sha256_hash)
    assert excinfo.value.code is bramble.ErrorCode.PERSISTED_QUERY_NOT_FOUND


def test_different_queries_get_different_cache_entries() -> None:
    schema = _schema()
    first_query = 'query { greet(name: "Ada") }'
    second_query = 'query { greet(name: "Bob") }'

    schema.resolve_persisted_query(_hash(first_query), query=first_query)

    with pytest.raises(bramble.GraphQLError) as excinfo:
        schema.resolve_persisted_query(_hash(second_query))
    assert excinfo.value.code is bramble.ErrorCode.PERSISTED_QUERY_NOT_FOUND


def test_prepare_persisted_query_returns_the_cached_document_for_reuse() -> None:
    schema = _schema()
    query_text = 'query { greet(name: "hello") }'
    sha256_hash = _hash(query_text)

    registration = schema.prepare_persisted_query(sha256_hash, query=query_text)
    assert registration.cache_hit is False

    replay = schema.prepare_persisted_query(sha256_hash)
    assert replay.cache_hit is True
    assert replay.document is not None


def test_executing_a_prepared_document_skips_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    """A cache hit has to be cheaper than a cold request, or the cache is only a protocol gate.
    Passing the prepared document to `execute` bypasses parse+validate entirely.
    """
    schema = _schema()
    query_text = 'query { greet(name: "hello") }'
    sha256_hash = _hash(query_text)
    schema.prepare_persisted_query(sha256_hash, query=query_text)

    calls: list[object] = []
    real_validate = bramble._execution.validate_document
    monkeypatch.setattr(
        bramble._execution,
        "validate_document",
        lambda document, compiled, operation_name: (
            calls.append(document),
            real_validate(document, compiled, operation_name),
        )[1],
    )

    prepared = schema.prepare_persisted_query(sha256_hash)
    result = schema.execute(None, document=prepared.document)

    assert result == {"data": {"greet": "hello"}}
    assert calls == []


def test_a_replayed_document_reports_no_query_source_to_resolvers() -> None:
    """A hash-only replay genuinely has no query text -- the client never sent it. `Info.query` is
    `None` rather than an AST printed back out into a string the client never wrote.
    """
    observed: list[str | None] = []

    @bramble.type
    class Query:
        @bramble.field
        def greet(info: bramble.Info) -> str:
            observed.append(info.query)
            return "hello"

    schema = bramble.Schema(query=Query)
    query_text = "query { greet }"
    sha256_hash = _hash(query_text)

    schema.prepare_persisted_query(sha256_hash, query=query_text)
    prepared = schema.prepare_persisted_query(sha256_hash)
    schema.execute(None, document=prepared.document)

    assert observed == [None]
