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
import time
import warnings
from collections.abc import AsyncGenerator
from typing import Annotated, Any

import pytest

import bramble
from bramble.schema.config import SchemaConfig

# Shared trace list. Each test clears it first; extensions append to it so ordering assertions can
# check the exact sequence rather than merely that a hook fired.
TRACE: list[str] = []


@bramble.type
class _Query:
    @bramble.field
    def greeting() -> str:
        return "hello"

    @bramble.field
    def boom() -> str:
        raise ValueError("resolver exploded")


def _schema(**kwargs: Any) -> bramble.Schema:
    return bramble.Schema(query=_Query, **kwargs)


# --- Lifecycle hook ordering ----------------------------------------------------------------------


class _Traced(bramble.SchemaExtension):
    """Records every hook it implements. Subclasses set `label`."""

    label = "?"

    def on_operation(self):
        TRACE.append(f"{self.label} operation before")
        yield
        TRACE.append(f"{self.label} operation after")

    def on_parse(self):
        TRACE.append(f"{self.label} parse before")
        yield
        TRACE.append(f"{self.label} parse after")

    def on_validate(self):
        TRACE.append(f"{self.label} validate before")
        yield
        TRACE.append(f"{self.label} validate after")

    def on_execute(self):
        TRACE.append(f"{self.label} execute before")
        yield
        TRACE.append(f"{self.label} execute after")


class _First(_Traced):
    label = "first"


class _Second(_Traced):
    label = "second"


def test_lifecycle_hooks_fire_in_pipeline_order() -> None:
    TRACE.clear()
    _schema(extensions=[_First]).execute("{ greeting }")

    assert TRACE == [
        "first operation before",
        "first parse before",
        "first parse after",
        "first validate before",
        "first validate after",
        "first execute before",
        "first execute after",
        "first operation after",
    ]


def test_two_extensions_nest_onion_style_in_list_order() -> None:
    """The first extension's "before" runs first and its "after" runs *last*, so an outer tracing
    span genuinely contains the inner one. Asserting the exact sequence, not just that hooks fired.
    """
    TRACE.clear()
    _schema(extensions=[_First, _Second]).execute("{ greeting }")

    assert TRACE == [
        "first operation before",
        "second operation before",
        "first parse before",
        "second parse before",
        "second parse after",
        "first parse after",
        "first validate before",
        "second validate before",
        "second validate after",
        "first validate after",
        "first execute before",
        "second execute before",
        "second execute after",
        "first execute after",
        "second operation after",
        "first operation after",
    ]


def test_reversing_the_list_reverses_the_nesting() -> None:
    TRACE.clear()
    _schema(extensions=[_Second, _First]).execute("{ greeting }")

    assert TRACE[0] == "second operation before"
    assert TRACE[-1] == "second operation after"


# --- Sync and async hook styles --------------------------------------------------------------------


class _SyncStyle(bramble.SchemaExtension):
    def on_operation(self):
        TRACE.append("sync before")
        yield
        TRACE.append("sync after")


class _AsyncStyle(bramble.SchemaExtension):
    async def on_operation(self):
        TRACE.append("async before")
        await asyncio.sleep(0)
        yield
        await asyncio.sleep(0)
        TRACE.append("async after")


def test_sync_and_async_generator_hooks_both_work_and_interleave_correctly() -> None:
    TRACE.clear()
    _schema(extensions=[_SyncStyle, _AsyncStyle]).execute("{ greeting }")

    assert TRACE == ["sync before", "async before", "async after", "sync after"]


# --- Error handling ---------------------------------------------------------------------------------


class _RaisesOnEntry(bramble.SchemaExtension):
    def on_operation(self):
        TRACE.append("raiser before")
        raise RuntimeError("hook exploded")
        yield  # pragma: no cover


class _Outer(bramble.SchemaExtension):
    def on_operation(self):
        TRACE.append("outer before")
        yield
        TRACE.append("outer after")


def test_a_hook_raising_on_entry_unwinds_already_entered_hooks() -> None:
    """Every already-entered hook is *unwound* -- the exception is thrown into it at its `yield`,
    exactly as for any context manager. Code after a bare `yield` is therefore skipped, which is
    standard Python semantics; a hook that must clean up regardless says so with `try`/`finally`
    (see the next test).
    """
    TRACE.clear()
    schema = _schema(extensions=[_Outer, _RaisesOnEntry])

    with pytest.raises(RuntimeError, match="hook exploded"):
        schema.execute("{ greeting }")

    assert TRACE == ["outer before", "raiser before"]


def test_a_hook_using_try_finally_always_completes_its_after_phase() -> None:
    """The guarantee that actually matters: an entered hook is always given the chance to clean up,
    however the wrapped step ends. This is the same shape as `DependencyScope.aclose()` and the
    subscription generator teardown elsewhere in the codebase.
    """
    TRACE.clear()

    class Cleanup(bramble.SchemaExtension):
        def on_operation(self):
            TRACE.append("cleanup before")
            try:
                yield
            finally:
                TRACE.append("cleanup after")

    schema = _schema(extensions=[Cleanup, _RaisesOnEntry])

    with pytest.raises(RuntimeError, match="hook exploded"):
        schema.execute("{ greeting }")

    assert TRACE == ["cleanup before", "raiser before", "cleanup after"]


def test_a_hook_may_suppress_an_error_from_the_wrapped_step() -> None:
    # A hook is a context manager, so catching around the `yield` swallows the failure, exactly as
    # `contextlib.suppress` would.
    class Masking(bramble.SchemaExtension):
        def on_operation(self):
            try:
                yield
            except bramble.GraphQLError:
                TRACE.append("masked")

    TRACE.clear()
    _schema(extensions=[Masking]).execute("{ unclosed")

    assert TRACE == ["masked"]


def test_a_failing_step_still_runs_every_after_phase() -> None:
    TRACE.clear()

    @bramble.type
    class Query:
        @bramble.field
        def ok() -> str:
            return "x"

    schema = bramble.Schema(query=Query, extensions=[_First, _Second])

    # A malformed query fails during parse, inside both extensions' operation hooks.
    with pytest.raises(bramble.GraphQLError):
        schema.execute("{ unclosed")

    # Bare `yield` hooks, so post-yield code is skipped on the way out -- see
    # `test_a_hook_using_try_finally_always_completes_its_after_phase`. What this pins down is that
    # the failure happens inside the parse span of both extensions, not somewhere else.
    assert TRACE == [
        "first operation before",
        "second operation before",
        "first parse before",
        "second parse before",
    ]


def test_a_resolver_error_does_not_prevent_after_phases() -> None:
    # A field error is captured into `errors`, not raised -- the hooks must still complete normally.
    TRACE.clear()
    result = _schema(extensions=[_First]).execute("{ boom }")

    assert result["data"] is None
    assert TRACE[-1] == "first operation after"


# --- get_results and the execution context -----------------------------------------------------------


class _Timing(bramble.SchemaExtension):
    """The canonical Strawberry tracing shape: measure across the `yield`, report afterwards."""

    def on_operation(self):
        start = time.perf_counter()
        yield
        self.execution_context.extensions_results["timing_ms"] = (time.perf_counter() - start) * 1000


def test_results_written_after_the_yield_still_reach_the_response() -> None:
    """`get_results()` has to run *after* `on_operation`'s "after" half, or the usual timing
    extension shape silently reports nothing.
    """
    result = _schema(extensions=[_Timing]).execute("{ greeting }")

    assert result["data"] == {"greeting": "hello"}
    assert result["extensions"]["timing_ms"] >= 0


class _StaticResults(bramble.SchemaExtension):
    def get_results(self) -> dict[str, Any]:
        return {"static": "value"}


class _AsyncResults(bramble.SchemaExtension):
    async def get_results(self) -> dict[str, Any]:
        await asyncio.sleep(0)
        return {"async": "value"}


def test_get_results_merges_sync_and_async_extensions() -> None:
    result = _schema(extensions=[_StaticResults, _AsyncResults]).execute("{ greeting }")

    assert result["extensions"] == {"static": "value", "async": "value"}


def test_no_extensions_key_when_nothing_reports() -> None:
    # A response shouldn't grow an empty `extensions` object just because an extension exists.
    assert "extensions" not in _schema(extensions=[_First]).execute("{ greeting }")


class _ContextInspector(bramble.SchemaExtension):
    def on_operation(self):
        yield
        ctx = self.execution_context
        TRACE.append(f"query={ctx.query!r} op={ctx.operation_type} vars={ctx.variable_values} ctx={ctx.context}")


def test_execution_context_exposes_the_request() -> None:
    TRACE.clear()
    _schema(extensions=[_ContextInspector]).execute(
        "query Named { greeting }", operation_name="Named", context={"user": "ada"}
    )

    assert TRACE == ["query='query Named { greeting }' op=query vars={} ctx={'user': 'ada'}"]


# --- SchemaExtension.resolve --------------------------------------------------------------------------


class _CountsFields(bramble.SchemaExtension):
    async def resolve(self, next_, source, info, **kwargs):
        TRACE.append(f"resolve {info.field_name}")
        result = next_(source, info, **kwargs)
        if hasattr(result, "__await__"):
            result = await result
        return result


def test_schema_extension_resolve_wraps_every_field() -> None:
    TRACE.clear()
    result = _schema(extensions=[_CountsFields]).execute("{ a: greeting b: greeting }")

    assert result["data"] == {"a": "hello", "b": "hello"}
    assert TRACE == ["resolve greeting", "resolve greeting"]


# --- FieldExtension -----------------------------------------------------------------------------------


class _UpperCase(bramble.FieldExtension):
    async def resolve_async(self, next_, source, info, **kwargs):
        return (await next_(source, info, **kwargs)).upper()


class _Exclaim(bramble.FieldExtension):
    def resolve(self, next_, source, info, **kwargs):
        return next_(source, info, **kwargs)


class _RequiresUser(bramble.FieldExtension):
    """Authorization shape: refuses without ever calling the resolver."""

    async def resolve_async(self, next_, source, info, **kwargs):
        if not (info.context or {}).get("user"):
            raise bramble.GraphQLError("Forbidden", code=bramble.ErrorCode.FIELD_RESOLUTION_FAILED)
        return await next_(source, info, **kwargs)


class _Memoise(bramble.FieldExtension):
    """Caching shape: replaces the resolved value on a hit, skipping the resolver entirely."""

    def __init__(self) -> None:
        self.cache: dict[tuple, Any] = {}
        self.misses = 0

    async def resolve_async(self, next_, source, info, **kwargs):
        key = tuple(sorted(kwargs.items()))
        if key not in self.cache:
            self.misses += 1
            self.cache[key] = await next_(source, info, **kwargs)
        return self.cache[key]


_MEMO = _Memoise()


@bramble.type
class _FieldExtQuery:
    @bramble.field(extensions=[_UpperCase])
    def loud() -> str:
        return "hello"

    @bramble.field(extensions=[_RequiresUser])
    def secret() -> str | None:
        raise AssertionError("a refused field must never run its resolver")

    @bramble.field(extensions=[_MEMO])
    def expensive(seed: int) -> int:
        return seed * 2


def test_field_extension_modifies_the_resolved_value() -> None:
    assert bramble.Schema(query=_FieldExtQuery).execute("{ loud }") == {"data": {"loud": "HELLO"}}


def test_field_extension_can_short_circuit_without_calling_the_resolver() -> None:
    schema = bramble.Schema(query=_FieldExtQuery)
    result = schema.execute("{ secret }", context={})

    assert result["data"] == {"secret": None}
    assert result["errors"][0]["message"] == "Forbidden"


def test_field_extension_caching_skips_the_resolver_on_a_hit() -> None:
    _MEMO.cache.clear()
    _MEMO.misses = 0
    schema = bramble.Schema(query=_FieldExtQuery)

    assert schema.execute("{ expensive(seed: 21) }")["data"] == {"expensive": 42}
    assert schema.execute("{ expensive(seed: 21) }")["data"] == {"expensive": 42}
    assert _MEMO.misses == 1, "the second call must come from the cache"

    assert schema.execute("{ expensive(seed: 5) }")["data"] == {"expensive": 10}
    assert _MEMO.misses == 2, "a different argument is a different cache entry"


class _TraceExt(bramble.FieldExtension):
    def __init__(self, label: str) -> None:
        self.label = label

    async def resolve_async(self, next_, source, info, **kwargs):
        TRACE.append(f"{self.label} before")
        result = await next_(source, info, **kwargs)
        TRACE.append(f"{self.label} after")
        return result


@bramble.type
class _OrderQuery:
    @bramble.field(extensions=[_TraceExt("outer"), _TraceExt("inner")])
    def value() -> str:
        TRACE.append("resolver")
        return "v"


def test_field_extensions_compose_onion_style_in_list_order() -> None:
    TRACE.clear()
    bramble.Schema(query=_OrderQuery).execute("{ value }")

    assert TRACE == ["outer before", "inner before", "resolver", "inner after", "outer after"]


class _SyncOnly(bramble.FieldExtension):
    def resolve(self, next_, source, info, **kwargs):
        return next_(source, info, **kwargs)


@bramble.type
class _MixedQuery:
    # A sync-only and an async-only extension on one field. Strawberry raises a TypeError for this
    # ordering; bramble has a single async execution path, so it simply works.
    @bramble.field(extensions=[_SyncOnly, _UpperCase])
    def mixed() -> str:
        return "hello"


def test_sync_and_async_field_extensions_mix_without_a_type_error() -> None:
    assert bramble.Schema(query=_MixedQuery).execute("{ mixed }") == {"data": {"mixed": "HELLO"}}


class _ArgumentMapper(bramble.FieldExtension):
    def map_arguments(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        return {**kwargs, "seed": kwargs["seed"] + 1}

    async def resolve_async(self, next_, source, info, **kwargs):
        return await next_(source, info, **kwargs)


@bramble.type
class _MapQuery:
    @bramble.field(extensions=[_ArgumentMapper])
    def doubled(seed: int) -> int:
        return seed * 2


def test_field_extension_can_reshape_arguments() -> None:
    assert bramble.Schema(query=_MapQuery).execute("{ doubled(seed: 1) }") == {"data": {"doubled": 4}}


class _AppliedRecorder(bramble.FieldExtension):
    seen: list[str] = []  # noqa: RUF012

    def apply(self, field: Any) -> None:
        _AppliedRecorder.seen.append(field.name)


def test_apply_runs_once_at_schema_build_time() -> None:
    _AppliedRecorder.seen.clear()

    @bramble.type
    class Query:
        greeting: str = bramble.field(default="hi", extensions=[_AppliedRecorder])

    schema = bramble.Schema(query=Query)
    schema.execute("{ greeting }", root_value=Query())
    schema.execute("{ greeting }", root_value=Query())

    assert _AppliedRecorder.seen == ["greeting"], "apply() is build-time, not per-request"


# --- Fields with no resolver of their own -------------------------------------------------------------

# A data field's extensions used to be registered, `apply()`-ed, and then never run: reading the
# attribute off the parent returned before any chain was built. Permissions were already honoured on
# that same path (see `docs/guides/permissions.md`), so the two disagreed about whether a field
# without a resolver participates in the pipeline. It does.


class _Shout(bramble.FieldExtension):
    async def resolve_async(self, next_, source, info, **kwargs):
        return (await next_(source, info, **kwargs)).upper()


@bramble.type
class _DataFieldQuery:
    decorated: str = bramble.field(default="quiet", extensions=[_Shout])
    plain: str = bramble.field(default="quiet")


# Deliberately not the query root: `Schema()` re-decorates a *subclass* of the root to inject the
# introspection meta-fields, so the root's own chains cache on that subclass rather than on the
# class written here. A nested type is the only place the cache is observable.
@bramble.type
class _DataFieldChild:
    value: str = bramble.field(default="x", extensions=[_Shout])


@bramble.type
class _NestedQuery:
    @bramble.field
    def child() -> _DataFieldChild:
        return _DataFieldChild()


def test_field_extensions_run_on_a_field_without_a_resolver() -> None:
    result = bramble.Schema(query=_DataFieldQuery).execute(
        "{ decorated plain }", root_value=_DataFieldQuery()
    )
    assert result["data"] == {"decorated": "QUIET", "plain": "quiet"}


def test_schema_extension_resolve_sees_fields_without_a_resolver() -> None:
    """`SchemaExtension.resolve` is documented as wrapping *every* field resolution, which has to
    include the ones backed by an attribute read rather than a resolver.
    """
    seen: list[str] = []

    class Watcher(bramble.SchemaExtension):
        async def resolve(self, next_, source, info, **kwargs):
            seen.append(info.python_name)
            return await next_(source, info, **kwargs)

    schema = bramble.Schema(query=_DataFieldQuery, extensions=[Watcher])
    schema.execute("{ decorated plain }", root_value=_DataFieldQuery())

    assert seen == ["decorated", "plain"]


def test_a_data_field_chain_is_built_once_and_cached() -> None:
    schema = bramble.Schema(query=_NestedQuery)
    schema.execute("{ child { value } }")
    first = _DataFieldChild.__dict__["__bramble_field_chains__"]["value"]
    schema.execute("{ child { value } }")
    assert _DataFieldChild.__dict__["__bramble_field_chains__"]["value"] is first


def test_a_data_field_chain_respects_each_schemas_own_default_resolver() -> None:
    """The chain is cached on the class, so it must read `default_resolver` from the executing
    schema rather than closing over whichever one built it first.
    """

    @bramble.type
    class Query:
        value: str = bramble.field(default="x", extensions=[_Shout])

    attribute_schema = bramble.Schema(query=Query)
    dict_schema = bramble.Schema(
        query=Query,
        config=SchemaConfig(default_resolver=lambda parent, name: parent["value"]),
    )

    assert attribute_schema.execute("{ value }", root_value=Query())["data"] == {"value": "X"}
    assert dict_schema.execute("{ value }", root_value={"value": "y"})["data"] == {"value": "Y"}


# --- Interaction with the rest of the field pipeline ---------------------------------------------------


def test_field_extensions_run_inside_the_operation_directive_chain() -> None:
    """A field extension wraps *producing* the value; an operation directive transforms the value
    the client asked to transform. So a caching extension caches the resolver's own output, not a
    client's `@suffix`-ed view of it -- the only ordering that makes caching correct.
    """
    TRACE.clear()
    result = bramble.Schema(query=_DirectiveQuery, directives=[_suffix]).execute("{ tagged @suffix }")

    assert result["data"] == {"tagged": "EXT(base)-directive"}
    assert TRACE == ["ext", "directive"], "the extension sees the raw value; the directive sees the extension's"


class _TagExt(bramble.FieldExtension):
    async def resolve_async(self, next_, source, info, **kwargs):
        TRACE.append("ext")
        return f"EXT({await next_(source, info, **kwargs)})"


@bramble.directive(locations=[bramble.DirectiveLocation.FIELD], name="suffix")
def _suffix(value: bramble.DirectiveValue[str]) -> str:
    TRACE.append("directive")
    return f"{value}-directive"


@bramble.type
class _DirectiveQuery:
    @bramble.field(extensions=[_TagExt])
    def tagged() -> str:
        return "base"


def test_field_extensions_coexist_with_dependency_injection() -> None:
    """A field extension wraps the resolver, and the resolver's own `Depends[T]` are resolved
    inside that -- so per-request caching still applies across two fields sharing one provider.
    """
    _DI_CALLS.clear()
    schema = bramble.Schema(query=_DIQuery)

    result = schema.execute("{ a b }")

    assert result["data"] == {"a": "INJECTED", "b": "INJECTED"}
    assert _DI_CALLS == ["provider"], "one provider call for both fields -- caching survived"


_DI_CALLS: list[str] = []


async def _di_provider() -> str:
    _DI_CALLS.append("provider")
    return "injected"


@bramble.type
class _DIQuery:
    @bramble.field(extensions=[_UpperCase])
    def a(value: Annotated[str, bramble.Depends(_di_provider)]) -> str:
        return value

    @bramble.field(extensions=[_UpperCase])
    def b(value: Annotated[str, bramble.Depends(_di_provider)]) -> str:
        return value


# --- Streaming ------------------------------------------------------------------------------------------


class _StreamWatcher(bramble.SchemaExtension):
    def on_stream_result(self, result):
        TRACE.append(f"stream {sorted(result)}")
        yield


@bramble.type
class _SubQuery:
    ok: bool = True


@bramble.type
class _Subscription:
    @bramble.subscription
    async def ticks() -> AsyncGenerator[int, None]:
        for index in range(2):
            yield index


def test_on_stream_result_fires_for_each_subscription_payload() -> None:
    TRACE.clear()
    schema = bramble.Schema(query=_SubQuery, subscription=_Subscription, extensions=[_StreamWatcher])

    async def run() -> list[dict]:
        return [payload async for payload in schema.subscribe_async("subscription { ticks }")]

    payloads = asyncio.run(run())

    assert len(payloads) == 2
    assert TRACE == ["stream ['data']", "stream ['data']"]


# --- Strawberry compatibility smoke test -------------------------------------------------------------------


def test_a_strawberry_style_tracing_extension_ports_unchanged() -> None:
    """A realistic Strawberry extension, copied shape-for-shape with only the import changed --
    generator hook, `self.execution_context`, `get_results()` returning a dict.
    """

    class MyTracing(bramble.SchemaExtension):
        def on_operation(self):
            self._start = time.perf_counter()
            yield
            self._duration = time.perf_counter() - self._start

        def get_results(self) -> dict[str, Any]:
            return {"tracing": {"duration_ms": self._duration * 1000}}

    result = _schema(extensions=[MyTracing]).execute("{ greeting }")

    assert result["data"] == {"greeting": "hello"}
    assert result["extensions"]["tracing"]["duration_ms"] >= 0


def test_an_extension_instance_is_reused_while_a_class_is_per_request() -> None:
    class Counter(bramble.SchemaExtension):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            self.count = 0

        def on_operation(self):
            self.count += 1
            yield
            TRACE.append(f"count={self.count}")

    TRACE.clear()
    shared = Counter()
    with pytest.warns(DeprecationWarning, match="shared by every request"):
        schema = bramble.Schema(query=_Query, extensions=[shared])
    schema.execute("{ greeting }")
    schema.execute("{ greeting }")
    assert TRACE == ["count=1", "count=2"], "an instance carries state across requests"

    TRACE.clear()
    schema = bramble.Schema(query=_Query, extensions=[Counter])
    schema.execute("{ greeting }")
    schema.execute("{ greeting }")
    assert TRACE == ["count=1", "count=1"], "a class is instantiated fresh per request"


# --- Fields whose arguments collide with the hook's own parameter names -----------------------------


class _ShoutArg(bramble.FieldExtension):
    async def resolve_async(self, next_, source, info, /, **kwargs):
        return (await next_(source, info, **kwargs)).upper()


class _WatchArg(bramble.SchemaExtension):
    async def resolve(self, next_, source, info, /, **kwargs):
        return await next_(source, info, **kwargs)


@bramble.type
class _CollidingArgumentQuery:
    @bramble.field(extensions=[_ShoutArg()])
    def log(source: str | None = None, info: str | None = None) -> str:
        return f"source={source} info={info}"


def test_a_field_argument_may_be_named_like_the_hooks_own_parameters() -> None:
    """`source`/`info` are perfectly ordinary GraphQL argument names -- an activity log filtered by
    `source`, say. They arrive in `**kwargs`, so unless every hook in the chain takes its own
    `source`/`info` positionally, Python raises "got multiple values for argument 'source'" before
    the resolver runs. The `/` in each wrapper and each documented hook signature is what keeps the
    two namespaces apart.
    """
    schema = bramble.Schema(query=_CollidingArgumentQuery, extensions=[_WatchArg])

    result = schema.execute('{ log(source: "api", info: "x") }')

    assert result.get("errors") is None
    assert result["data"] == {"log": "SOURCE=API INFO=X"}


# --- a factory callable, for an extension that needs constructor arguments ------------------------
#
# The old choice was between a class you cannot configure and an instance that is unsafe: bramble
# assigns `execution_context` onto a registered instance per request, so any hook reading
# `self.execution_context` -- the documented way to reach the result and errors -- observes another
# request's context as soon as two overlap. A factory is called per request, so the object a hook
# reads belongs to that request.


def test_a_factory_callable_is_accepted_and_configured() -> None:
    class Labelled(bramble.SchemaExtension):
        def __init__(self, label: str, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            self.label = label

        def get_results(self) -> dict[str, Any]:
            return {"label": self.label}

    schema = bramble.Schema(query=_Query, extensions=[lambda: Labelled("configured")])
    result = schema.execute("{ greeting }")

    assert result["extensions"]["label"] == "configured"


def test_a_factory_produces_a_fresh_instance_per_request() -> None:
    class Counter(bramble.SchemaExtension):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            self.count = 0

        def on_operation(self):
            self.count += 1
            yield
            TRACE.append(f"count={self.count}")

    TRACE.clear()
    schema = bramble.Schema(query=_Query, extensions=[lambda: Counter()])
    schema.execute("{ greeting }")
    schema.execute("{ greeting }")

    assert TRACE == ["count=1", "count=1"], "a factory must not carry state across requests"


def test_a_factory_gets_its_own_execution_context() -> None:
    """The race the deprecation exists for: with a shared instance every request overwrites the
    same attribute, so a hook can read a context belonging to a request it is not part of.
    """
    # The objects themselves, not their `id()`s -- an id is only unique while its object is alive,
    # and the first context is collectable by the time the second request runs.
    seen: list[Any] = []

    class Recorder(bramble.SchemaExtension):
        def on_operation(self):
            seen.append(self.execution_context)
            yield

    schema = bramble.Schema(query=_Query, extensions=[lambda: Recorder()])
    schema.execute("{ greeting }")
    schema.execute("{ greeting }")

    assert seen[0] is not seen[1], "each request must see its own execution context"


def test_a_factory_returning_a_non_extension_still_fails() -> None:
    """Deferred to request time -- a factory's result cannot be inspected at build time without
    constructing an extension there.
    """
    schema = bramble.Schema(query=_Query, extensions=[lambda: object()])

    with pytest.raises(AttributeError):
        schema.execute("{ greeting }")


def test_passing_a_class_is_not_deprecated() -> None:
    class Noop(bramble.SchemaExtension):
        pass

    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        bramble.Schema(query=_Query, extensions=[Noop])


def test_passing_a_factory_is_not_deprecated() -> None:
    class Noop(bramble.SchemaExtension):
        pass

    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        bramble.Schema(query=_Query, extensions=[lambda: Noop()])
