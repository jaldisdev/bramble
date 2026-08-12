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
import inspect
import typing
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Annotated, Any

from bramble._bramble import SchemaError
from bramble._resolver import Depends, Info

# --- Classification (§3c) -------------------------------------------------------------------------
#
# Mirrors the Rust-side `classify_parameters` (`crates/bramble-py/src/resolver_binding.rs`) --
# specifically its `Info`/`Depends[T]` recognition -- closely enough that both agree on every
# resolver/directive parameter's classification, without literally sharing code across the Rust/
# Python boundary. This is a deliberate split, not an oversight: Rust's job is schema *shape* (which
# parameters are GraphQL-visible arguments, decided once at schema-build time, with no Python
# callables threaded through `bramble_core`'s own Python-free IR); this module's job is runtime
# *behavior* (which provider a `Depends[T]` parameter actually calls, with what caching/teardown),
# which needs the live marker object, not just its parameter name. A provider function's own
# signature is never seen by Rust at all -- providers aren't part of the GraphQL type graph, so
# classifying one only ever happens here, lazily, the first time a dependency chain reaches it (not
# eagerly at `Schema()` build time, unlike every other schema-shape validation in this project).


@dataclass(frozen=True, slots=True)
class _ParameterClassification:
    info_parameter: str | None
    dependencies: dict[str, Depends]


_CLASSIFICATION_CACHE: dict[Callable[..., Any], _ParameterClassification] = {}


def _unwrap_annotated(annotation: Any) -> tuple[Any, tuple[Any, ...]]:
    if typing.get_origin(annotation) is Annotated:
        args = typing.get_args(annotation)
        return args[0], args[1:]
    return annotation, ()


def _classify_parameters(func: Callable[..., Any], cls: type | None) -> _ParameterClassification:
    """Classifies `func`'s parameters by annotation alone, exactly like the Rust classifier does
    for a resolver/directive: `Info` -> the execution context, `Annotated[T, bramble.Depends(...)]`
    -> a dependency to resolve before calling `func`. Cached by function identity -- `func` is
    typically a resolver or directive already classified once by Rust (this just adds the `Depends`
    half Rust doesn't carry through its own IR), or a provider called on every request that uses it,
    so redoing `inspect.signature`/`typing.get_type_hints` on every single call would be pure waste.
    """
    cached = _CLASSIFICATION_CACHE.get(func)
    if cached is not None:
        return cached

    signature = inspect.signature(func)
    localns: dict[str, Any] = {}
    if cls is not None:
        localns[cls.__name__] = cls

    try:
        hints = typing.get_type_hints(func, localns=localns, include_extras=True)
    except NameError as error:
        raise SchemaError(
            f"could not resolve parameter annotations for "
            f"'{getattr(func, '__qualname__', func)}': {error}"
        ) from error

    info_parameter: str | None = None
    dependencies: dict[str, Depends] = {}

    for name, parameter in signature.parameters.items():
        if parameter.annotation is inspect.Parameter.empty:
            # Already rejected by the Rust classifier for a resolver/directive (every parameter
            # there must be annotated); a provider is never schema-validated by Rust at all, so an
            # unannotated provider parameter surfaces here instead, as a plain missing-argument
            # `TypeError` when the provider is actually called -- not silently misclassified.
            continue

        annotation = hints.get(name, parameter.annotation)
        origin = typing.get_origin(annotation)

        if annotation is Info or origin is Info:
            info_parameter = name
            continue

        _, metadata = _unwrap_annotated(annotation)
        marker = next((item for item in metadata if isinstance(item, Depends)), None)
        if marker is not None:
            dependencies[name] = marker

    classification = _ParameterClassification(info_parameter=info_parameter, dependencies=dependencies)
    _CLASSIFICATION_CACHE[func] = classification
    return classification


# --- Runtime resolution, caching, teardown (§3c) --------------------------------------------------


@dataclass(slots=True)
class DependencyScope:
    """One dependency-injection cache -- scoped to a single query/mutation request
    (`execute_async`/`execute`/`execute_incremental`), or to one active subscription's own lifetime
    (`subscribe_async`; created once before its event loop starts, reused for every event, never
    per-connection or per-emitted-event). `cache` stores each provider's own in-flight/completed
    `asyncio.Task`, keyed by provider identity -- storing the *Task*, not just its eventual result,
    is what gives single-flight for free: two sibling resolvers needing the same dependency at
    roughly the same time both `await` the identical Task object, so the provider still only runs
    once, however many concurrent callers there are. `seeded` holds a `resolved_dependencies=` value
    (see `Schema.execute_async`) -- never invoked, never torn down, since bramble never owned it.
    """

    cache: dict[int, "asyncio.Task[Any]"] = field(default_factory=dict)
    seeded: dict[int, Any] = field(default_factory=dict)
    generators: list[AsyncIterator[Any]] = field(default_factory=list)

    def seed(self, resolved_dependencies: dict[Callable[..., Any], Any] | None) -> None:
        for provider, value in (resolved_dependencies or {}).items():
            self.seeded[id(provider)] = value

    async def aclose(self) -> None:
        """Tears down every generator-based provider's own `finally` block (`agen.aclose()` throws
        `GeneratorExit` at its suspension point, running whatever cleanup follows its `yield`), in
        reverse creation order -- called exactly once, when this scope's own owning request/
        subscription ends, however it ends (normal completion, early consumer disconnect, or an
        error). Every generator gets a chance to close even if an earlier one's own teardown raises
        -- the first such error is still re-raised afterward, not silently dropped.
        """
        first_error: BaseException | None = None
        for agen in reversed(self.generators):
            try:
                await agen.aclose()
            except BaseException as error:  # noqa: BLE001 -- every generator must still get a chance to close; re-raised below.
                if first_error is None:
                    first_error = error
        if first_error is not None:
            raise first_error


async def _invoke_provider(
    provider: Callable[..., Any], kwargs: dict[str, Any]
) -> tuple[Any, AsyncIterator[Any] | None]:
    """Calls `provider`, returning its value plus (only for a generator-based provider) the still-
    open async generator for `DependencyScope.aclose()` to tear down later. Covers all three shapes
    §3c allows: a plain sync function (called directly), an `async def` (awaited), and an async-
    generator function (driven up to its first `yield`) -- the same `inspect.isawaitable` check
    `bramble._execution` already applies to a resolver's own return value covers a *sync* function
    that itself happens to return an awaitable (a rare but spec-legal provider shape), not just an
    `async def` one.
    """
    if inspect.isasyncgenfunction(provider):
        generator = provider(**kwargs)
        value = await generator.__anext__()
        return value, generator

    result = provider(**kwargs)
    if inspect.isawaitable(result):
        result = await result
    return result, None


async def _provider_kwargs(provider: Callable[..., Any], *, info: Info, scope: DependencyScope) -> dict[str, Any]:
    classification = _classify_parameters(provider, None)
    kwargs: dict[str, Any] = {}
    if classification.info_parameter is not None:
        kwargs[classification.info_parameter] = info
    for name, marker in classification.dependencies.items():
        kwargs[name] = await _resolve_dependency(marker, info=info, scope=scope)
    return kwargs


async def _resolve_dependency(marker: Depends, *, info: Info, scope: DependencyScope) -> Any:
    provider = marker.provider
    key = id(provider)

    if marker.use_cache and key in scope.seeded:
        return scope.seeded[key]

    async def _run() -> Any:
        kwargs = await _provider_kwargs(provider, info=info, scope=scope)
        value, generator = await _invoke_provider(provider, kwargs)
        if generator is not None:
            scope.generators.append(generator)
        return value

    if not marker.use_cache:
        return await _run()

    task = scope.cache.get(key)
    if task is None:
        task = asyncio.ensure_future(_run())
        scope.cache[key] = task
    return await task


async def resolve_dependencies(
    func: Callable[..., Any], *, cls: type | None = None, info: Info, scope: DependencyScope
) -> dict[str, Any]:
    """Resolves every `Depends[T]`-marked parameter on `func` (a resolver or a custom operation
    directive function) to its actual value, ready to merge into that function's own call kwargs.
    `func`'s own `Info` parameter, if any, is bound separately by the caller (already known from
    the schema-build-time-derived `field_info.info_parameter`/`directive_info.info_parameter`) --
    this only ever handles `Depends[T]`, recursively: a resolved provider's own signature is
    classified and resolved by this exact same mechanism (`_provider_kwargs`/`_resolve_dependency`
    above), which is what makes nested dependencies fall out of one shared rule rather than being a
    separately implemented feature.
    """
    classification = _classify_parameters(func, cls)
    kwargs: dict[str, Any] = {}
    for name, marker in classification.dependencies.items():
        kwargs[name] = await _resolve_dependency(marker, info=info, scope=scope)
    return kwargs
