from __future__ import annotations

import hashlib

import pytest

import bramble


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
