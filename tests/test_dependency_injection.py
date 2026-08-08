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
from collections.abc import AsyncGenerator, AsyncIterator
from typing import Annotated

import bramble
from bramble.directive import DirectiveLocation, DirectiveValue

# `typing.get_type_hints` (used by both the Rust classifier and bramble._dependency's own runtime
# one) can't see an enclosing test function's local scope -- under `from __future__ import
# annotations`, an annotation is just a string until resolved, and resolution only ever sees a
# function's own module globals (plus whatever localns bramble explicitly seeds). This bites not
# just a bare forward-referenced *type* (the well-known version of this gotcha elsewhere in the
# suite) but the *entire* annotation expression, including a `bramble.Depends(some_local_provider)`
# call -- so every provider function referenced from a resolver/directive's own annotation has to
# live at module level here, exactly like a referenced class would. Mutable state a given test
# needs to observe (call counts, open/close ordering) is a module-level container, reset at the
# start of each test that uses it.


async def _collect_n(generator: AsyncIterator[dict[str, object]], n: int) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    async for item in generator:
        results.append(item)
        if len(results) >= n:
            break
    return results


# --- Provider shapes: plain sync, async, generator (with teardown), nested -------------------------


def _sync_provider() -> str:
    return "sync-value"


async def _async_provider() -> str:
    return "async-value"


@bramble.type
class _SyncProviderQuery:
    @bramble.field
    def value(x: Annotated[str, bramble.Depends(_sync_provider)]) -> str:
        return x


@bramble.type
class _AsyncProviderQuery:
    @bramble.field
    async def value(x: Annotated[str, bramble.Depends(_async_provider)]) -> str:
        return x


@bramble.type
class _SyncResolverAsyncDependsQuery:
    @bramble.field
    def value(x: Annotated[str, bramble.Depends(_async_provider)]) -> str:
        return x.upper()


def test_plain_sync_provider() -> None:
    schema = bramble.Schema(query=_SyncProviderQuery)
    assert schema.execute("{ value }") == {"data": {"value": "sync-value"}}


def test_async_provider() -> None:
    schema = bramble.Schema(query=_AsyncProviderQuery)
    assert schema.execute("{ value }") == {"data": {"value": "async-value"}}


def test_sync_resolver_can_depend_on_an_async_provider() -> None:
    """§3c: a resolver never needs to be declared `async def` just because a dependency it uses has
    an async provider -- bramble resolves the dependency first, then calls the resolver with the
    already-materialized value.
    """
    schema = bramble.Schema(query=_SyncResolverAsyncDependsQuery)
    assert schema.execute("{ value }") == {"data": {"value": "ASYNC-VALUE"}}


def test_depends_parameter_is_excluded_from_graphql_arguments() -> None:
    field_info = _SyncProviderQuery.__bramble_type_info__.fields[0]
    assert [a.name for a in field_info.arguments] == []


# --- Nested dependencies -----------------------------------------------------------------------------


def _base_provider() -> int:
    return 10


def _doubled_provider(base: Annotated[int, bramble.Depends(_base_provider)]) -> int:
    return base * 2


@bramble.type
class _NestedQuery:
    @bramble.field
    def value(x: Annotated[int, bramble.Depends(_doubled_provider)]) -> int:
        return x


def test_nested_dependency_resolves_through_the_full_chain() -> None:
    schema = bramble.Schema(query=_NestedQuery)
    assert schema.execute("{ value }") == {"data": {"value": 20}}


# --- Shared classifier: resolver and directive agree ------------------------------------------------


def _shared_marker_provider() -> str:
    return "shared"


@bramble.type
class _ClassifierQuery:
    @bramble.field
    def value(x: Annotated[str, bramble.Depends(_shared_marker_provider)]) -> str:
        return x


@bramble.directive(locations=[DirectiveLocation.FIELD])
def classifier_directive(value: DirectiveValue[str], x: Annotated[str, bramble.Depends(_shared_marker_provider)]) -> str:
    return f"{value}-{x}"


def test_resolver_and_directive_bind_an_identical_depends_annotation_the_same_way() -> None:
    """DI Task 1's own acceptance criterion: a resolver and a custom operation directive function
    using identical `Annotated[T, bramble.Depends(provider)]` syntax both exclude it from their own
    GraphQL-visible argument list (the shared Rust classifier), and both resolve it to the same
    provider's value at runtime (the shared Python runtime resolution).
    """
    field_info = _ClassifierQuery.__bramble_type_info__.fields[0]
    assert [a.name for a in field_info.arguments] == []

    directive_info = classifier_directive.__bramble_directive_info__
    assert directive_info.value_parameter == "value"
    assert [a.name for a in directive_info.arguments] == []

    schema = bramble.Schema(query=_ClassifierQuery, directives=[classifier_directive])
    assert schema.execute("{ value @classifierDirective }") == {"data": {"value": "shared-shared"}}


# --- Info injection in a custom operation directive (previously unsupported) ------------------------


@bramble.directive(locations=[DirectiveLocation.FIELD])
def tag_with_field_name(value: DirectiveValue[str], info: bramble.Info) -> str:
    return f"{info.field_name}:{value}"


@bramble.type
class _InfoDirectiveQuery:
    @bramble.field
    def greeting() -> str:
        return "hi"


def test_operation_directive_supports_info_injection() -> None:
    schema = bramble.Schema(query=_InfoDirectiveQuery, directives=[tag_with_field_name])
    assert schema.execute("{ greeting @tagWithFieldName }") == {"data": {"greeting": "greeting:hi"}}


# --- Generator provider teardown ---------------------------------------------------------------------

_teardown_events: list[str] = []


async def _teardown_tracking_provider() -> AsyncIterator[str]:
    _teardown_events.append("open")
    try:
        yield "resource"
    finally:
        _teardown_events.append("close")


@bramble.type
class _TeardownQuery:
    @bramble.field
    async def value(x: Annotated[str, bramble.Depends(_teardown_tracking_provider)]) -> str:
        _teardown_events.append("use")
        return x

    @bramble.field
    async def a(x: Annotated[str, bramble.Depends(_teardown_tracking_provider)]) -> str:
        return x

    @bramble.field
    async def b(x: Annotated[str, bramble.Depends(_teardown_tracking_provider)]) -> str:
        return x


def test_generator_provider_runs_teardown_after_a_query_completes() -> None:
    _teardown_events.clear()
    schema = bramble.Schema(query=_TeardownQuery)
    result = asyncio.run(schema.execute_async("{ value }"))
    assert result == {"data": {"value": "resource"}}
    assert _teardown_events == ["open", "use", "close"]


def test_generator_provider_teardown_runs_once_even_when_shared_by_two_fields() -> None:
    _teardown_events.clear()
    schema = bramble.Schema(query=_TeardownQuery)
    result = asyncio.run(schema.execute_async("{ a b }"))
    assert result == {"data": {"a": "resource", "b": "resource"}}
    assert _teardown_events == ["open", "close"]


# --- Single-flight -------------------------------------------------------------------------------

_slow_provider_calls = {"n": 0}


async def _slow_provider() -> str:
    _slow_provider_calls["n"] += 1
    await asyncio.sleep(0.01)
    return "value"


@bramble.type
class _SingleFlightQuery:
    @bramble.field
    async def a(x: Annotated[str, bramble.Depends(_slow_provider)]) -> str:
        return x

    @bramble.field
    async def b(x: Annotated[str, bramble.Depends(_slow_provider)]) -> str:
        return x

    @bramble.field
    async def c(x: Annotated[str, bramble.Depends(_slow_provider)]) -> str:
        return x


def test_two_concurrent_sibling_fields_share_one_provider_call() -> None:
    """§3c's single-flight requirement: sibling resolvers needing the same uncached-yet dependency,
    scheduled concurrently (bramble resolves sibling fields via `asyncio.gather`), must result in
    exactly one provider call -- not one per resolver.
    """
    _slow_provider_calls["n"] = 0
    schema = bramble.Schema(query=_SingleFlightQuery)
    result = asyncio.run(schema.execute_async("{ a b c }"))
    assert result == {"data": {"a": "value", "b": "value", "c": "value"}}
    assert _slow_provider_calls["n"] == 1, _slow_provider_calls


# --- use_cache ---------------------------------------------------------------------------------------

_id_counter = {"n": 0}


def _make_id() -> int:
    _id_counter["n"] += 1
    return _id_counter["n"]


@bramble.type
class _UseCacheQuery:
    @bramble.field
    def cached_a(x: Annotated[int, bramble.Depends(_make_id)]) -> int:
        return x

    @bramble.field
    def cached_b(x: Annotated[int, bramble.Depends(_make_id)]) -> int:
        return x

    @bramble.field
    def uncached(x: Annotated[int, bramble.Depends(_make_id, use_cache=False)]) -> int:
        return x


def test_use_cache_true_is_the_default_and_shares_one_instance() -> None:
    _id_counter["n"] = 0
    schema = bramble.Schema(query=_UseCacheQuery)
    result = schema.execute("{ cachedA cachedB }")
    assert result["data"]["cachedA"] == result["data"]["cachedB"]


def test_use_cache_false_produces_a_distinct_instance_from_a_cached_sibling() -> None:
    _id_counter["n"] = 0
    schema = bramble.Schema(query=_UseCacheQuery)
    result = schema.execute("{ cachedA uncached }")
    assert result["data"]["cachedA"] != result["data"]["uncached"]


# --- resolved_dependencies pre-seeding -----------------------------------------------------------

_never_called_count = {"n": 0}


def _never_called_provider() -> str:
    _never_called_count["n"] += 1
    return "provider-value"


@bramble.type
class _SeedQuery:
    @bramble.field
    def value(x: Annotated[str, bramble.Depends(_never_called_provider)]) -> str:
        return x


def test_resolved_dependencies_seeds_a_value_without_calling_its_provider() -> None:
    _never_called_count["n"] = 0
    schema = bramble.Schema(query=_SeedQuery)
    result = schema.execute("{ value }", resolved_dependencies={_never_called_provider: "seeded-value"})
    assert result == {"data": {"value": "seeded-value"}}
    assert _never_called_count["n"] == 0


_close_calls = {"n": 0}


async def _generator_provider_for_seed_test() -> AsyncIterator[str]:
    try:
        yield "unused"  # pragma: no cover -- never actually called; a value is seeded instead.
    finally:
        _close_calls["n"] += 1


@bramble.type
class _SeedGeneratorQuery:
    @bramble.field
    def value(x: Annotated[str, bramble.Depends(_generator_provider_for_seed_test)]) -> str:
        return x


def test_resolved_dependencies_generator_provider_is_never_closed_by_bramble() -> None:
    """bramble never owned a seeded value -- it must not attempt to tear it down, even if the
    *provider* it stands in for would normally be a generator with its own teardown.
    """
    _close_calls["n"] = 0
    schema = bramble.Schema(query=_SeedGeneratorQuery)
    result = asyncio.run(
        schema.execute_async("{ value }", resolved_dependencies={_generator_provider_for_seed_test: "seeded"})
    )
    assert result == {"data": {"value": "seeded"}}
    assert _close_calls["n"] == 0


# --- Defer/stream interaction ----------------------------------------------------------------------


def _defer_provider() -> str:
    return "injected"


@bramble.type
class _DeferAuthor:
    @bramble.field
    def name(x: Annotated[str, bramble.Depends(_defer_provider)]) -> str:
        return x


@bramble.type
class _DeferrableQuery:
    @bramble.field
    def id() -> str:
        return "q1"

    @bramble.field
    def author() -> _DeferAuthor:
        return _DeferAuthor()


def test_dependency_injection_works_inside_a_deferred_field() -> None:
    schema = bramble.Schema(query=_DeferrableQuery, types=[_DeferAuthor])

    async def scenario() -> list[dict[str, object]]:
        return [
            payload
            async for payload in schema.execute_incremental("query { id ... @defer { author { name } } }")
        ]

    payloads = asyncio.run(scenario())
    assert payloads == [
        {"data": {"id": "q1"}, "hasNext": True},
        {"incremental": [{"data": {"author": {"name": "injected"}}, "path": []}], "hasNext": False},
    ]


# --- Subscriptions: per-subscription (not per-connection, not per-event) scope ----------------------

_subscription_connection_calls = {"n": 0}


async def _subscription_connection_provider(info: bramble.Info) -> str:
    _subscription_connection_calls["n"] += 1
    return f"conn-{_subscription_connection_calls['n']}"


@bramble.type
class _SubscriptionMessage:
    @bramble.field
    def text(x: Annotated[str, bramble.Depends(_subscription_connection_provider)]) -> str:
        return x


@bramble.type
class _SubscriptionQuery:
    ok: bool = True


@bramble.type
class _CountSubscription:
    @bramble.field
    async def count(
        upto: int, x: Annotated[str, bramble.Depends(_subscription_connection_provider)]
    ) -> AsyncGenerator[_SubscriptionMessage, None]:
        for _ in range(upto):
            yield _SubscriptionMessage()


def test_subscription_dependency_scope_spans_every_event_not_one_per_event() -> None:
    _subscription_connection_calls["n"] = 0
    schema = bramble.Schema(query=_SubscriptionQuery, subscription=_CountSubscription, types=[_SubscriptionMessage])

    async def scenario() -> list[dict[str, object]]:
        generator = schema.subscribe_async("subscription { count(upto: 3) { text } }")
        results = await _collect_n(generator, 3)
        await generator.aclose()
        return results

    results = asyncio.run(scenario())
    assert results == [{"data": {"count": {"text": "conn-1"}}}] * 3
    assert _subscription_connection_calls["n"] == 1, _subscription_connection_calls


def test_two_concurrent_subscriptions_get_independent_dependency_instances() -> None:
    _subscription_connection_calls["n"] = 0
    schema = bramble.Schema(query=_SubscriptionQuery, subscription=_CountSubscription, types=[_SubscriptionMessage])

    async def scenario() -> tuple[list[dict[str, object]], list[dict[str, object]]]:
        gen_a = schema.subscribe_async("subscription { count(upto: 2) { text } }")
        gen_b = schema.subscribe_async("subscription { count(upto: 2) { text } }")
        results_a = await _collect_n(gen_a, 2)
        results_b = await _collect_n(gen_b, 2)
        await gen_a.aclose()
        await gen_b.aclose()
        return results_a, results_b

    results_a, results_b = asyncio.run(scenario())
    assert _subscription_connection_calls["n"] == 2
    assert results_a != results_b


_subscription_teardown_events: list[str] = []


async def _subscription_teardown_provider() -> AsyncIterator[str]:
    _subscription_teardown_events.append("open")
    try:
        yield "resource"
    finally:
        _subscription_teardown_events.append("close")


@bramble.type
class _TeardownMessage:
    @bramble.field
    def text(x: Annotated[str, bramble.Depends(_subscription_teardown_provider)]) -> str:
        return x


@bramble.type
class _TeardownCountSubscription:
    @bramble.field
    async def count(
        upto: int, x: Annotated[str, bramble.Depends(_subscription_teardown_provider)]
    ) -> AsyncGenerator[_TeardownMessage, None]:
        for _ in range(upto):
            yield _TeardownMessage()


def test_subscription_teardown_runs_exactly_once_on_normal_completion() -> None:
    _subscription_teardown_events.clear()
    schema = bramble.Schema(
        query=_SubscriptionQuery, subscription=_TeardownCountSubscription, types=[_TeardownMessage]
    )

    async def scenario() -> list[dict[str, object]]:
        return [item async for item in schema.subscribe_async("subscription { count(upto: 2) { text } }")]

    results = asyncio.run(scenario())
    assert len(results) == 2
    assert _subscription_teardown_events == ["open", "close"]


def test_subscription_teardown_runs_exactly_once_on_early_unsubscribe() -> None:
    """Regression test for a real bug found while building this feature: `Schema.subscribe_async`/
    `execute_incremental` used to wrap their inner generator with a plain `async for ... yield`,
    which does *not* propagate an early `.aclose()` (a client disconnecting/unsubscribing) down to
    the inner generator at all -- so a `Depends` provider's own generator-based teardown would only
    ever run later, via Python's async-generator GC finalizer, not promptly as part of closing the
    subscription. Fixed by wrapping both in `try/finally: await generator.aclose()`.
    """
    _subscription_teardown_events.clear()
    schema = bramble.Schema(
        query=_SubscriptionQuery, subscription=_TeardownCountSubscription, types=[_TeardownMessage]
    )

    async def scenario() -> None:
        generator = schema.subscribe_async("subscription { count(upto: 100) { text } }")
        await _collect_n(generator, 1)
        await generator.aclose()

    asyncio.run(scenario())
    assert _subscription_teardown_events == ["open", "close"]


_error_teardown_events: list[str] = []


async def _error_teardown_provider() -> AsyncIterator[str]:
    _error_teardown_events.append("open")
    try:
        yield "resource"
    finally:
        _error_teardown_events.append("close")


@bramble.type
class _BrokenSubscription:
    @bramble.field
    async def broken(x: Annotated[str, bramble.Depends(_error_teardown_provider)]) -> AsyncGenerator[str, None]:
        yield x
        raise ValueError("boom")


def test_subscription_teardown_runs_exactly_once_on_error() -> None:
    _error_teardown_events.clear()
    schema = bramble.Schema(query=_SubscriptionQuery, subscription=_BrokenSubscription)

    async def scenario() -> None:
        generator = schema.subscribe_async("subscription { broken }")
        await generator.__anext__()
        try:
            await generator.__anext__()
        except ValueError:
            pass

    asyncio.run(scenario())
    assert _error_teardown_events == ["open", "close"]
