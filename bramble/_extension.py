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

import contextlib
import dataclasses
import inspect
import types
from collections.abc import AsyncIterator, Callable, Iterator, Sequence
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from bramble._error import GraphQLError
    from bramble._resolver import Info
    from bramble._schema import Schema
    from bramble._type import Field


@dataclasses.dataclass
class ExecutionContext:
    """The request a `SchemaExtension`'s hooks are wrapping, reachable as
    `self.execution_context`.

    Mutable and shared by every extension for one request: `extensions_results` is how an extension
    reports back into the response, and `result`/`errors` let a late hook inspect what happened.
    Fields that aren't known until a step completes (`operation_type`, `result`) start as `None`.
    """

    schema: "Schema"
    query: str | None
    operation_name: str | None
    variable_values: dict[str, Any]
    context: Any
    root_value: Any
    operation_type: str | None = None
    result: dict[str, Any] | None = None
    errors: list["GraphQLError"] = dataclasses.field(default_factory=list)
    #: Merged into the response's own `extensions` key. Extensions write their reporting here.
    extensions_results: dict[str, Any] = dataclasses.field(default_factory=dict)


class SchemaExtension:
    """Wraps a whole request -- parse, validate, execute -- and optionally every field resolution
    within it. Registered with `Schema(extensions=[...])`.

    Every lifecycle hook is a generator yielding exactly once: code before the `yield` runs before
    the step, code after runs once it finishes. Both `def` (sync) and `async def` (async) generators
    work; bramble drives either.

        class Timing(bramble.SchemaExtension):
            def on_operation(self):
                start = time.perf_counter()
                yield
                self.execution_context.extensions_results["timing"] = time.perf_counter() - start

    Registered as a class or an instance. A **class** is instantiated once per request, which is
    what makes per-request state (a start time, a counter) safe. An **instance** is reused as-is
    across every request, and keeping that concurrency-safe is the caller's problem.

    With several extensions the hooks nest in list order -- the first extension's "before" runs
    first and its "after" runs last. If a step raises, every already-entered hook still gets its
    "after" phase, in reverse order, before the error propagates.
    """

    execution_context: ExecutionContext

    def __init__(self, *, execution_context: ExecutionContext | None = None) -> None:
        if execution_context is not None:
            self.execution_context = execution_context

    def on_operation(self) -> Iterator[None] | AsyncIterator[None]:  # type: ignore[empty-body]
        """Wraps the entire request: parse, validate, execute, and result assembly."""
        yield None  # type: ignore[misc]

    def on_parse(self) -> Iterator[None] | AsyncIterator[None]:  # type: ignore[empty-body]
        """Wraps turning the query text into a parsed document."""
        yield None  # type: ignore[misc]

    def on_validate(self) -> Iterator[None] | AsyncIterator[None]:  # type: ignore[empty-body]
        """Wraps validating the parsed document against the schema."""
        yield None  # type: ignore[misc]

    def on_execute(self) -> Iterator[None] | AsyncIterator[None]:  # type: ignore[empty-body]
        """Wraps resolving the operation's fields."""
        yield None  # type: ignore[misc]

    def on_stream_result(self, result: dict[str, Any]) -> Iterator[None] | AsyncIterator[None]:  # type: ignore[empty-body]
        """Wraps each payload yielded by `execute_incremental`/`subscribe_async`.

        `result` may be mutated before the "after" half returns, to change what the transport sends.
        """
        yield None  # type: ignore[misc]

    async def resolve(self, next_: Callable[..., Any], source: Any, info: "Info", **kwargs: Any) -> Any:
        """Wraps *every* field resolution in the request -- the schema-wide counterpart to a
        `FieldExtension`. Call `next_(source, info, **kwargs)` to continue the chain.

        Left unimplemented by default, and extensions that don't override it are skipped entirely
        rather than adding a layer of no-op wrapping to every field.
        """
        result = next_(source, info, **kwargs)
        if inspect.isawaitable(result):
            result = await result
        return result

    def get_results(self) -> dict[str, Any] | Any:
        """Extra keys for the response's `extensions` object. May be sync or `async def`."""
        return {}

    @classmethod
    def _implements_resolve(cls) -> bool:
        return cls.resolve is not SchemaExtension.resolve


class FieldExtension:
    """Wraps a single field's resolver. Registered with `bramble.field(extensions=[...])`.

        class UpperCase(bramble.FieldExtension):
            async def resolve_async(self, next_, source, info, **kwargs):
                return (await next_(source, info, **kwargs)).upper()

    `next_` is the rest of the chain: the next extension, or the resolver itself at the end. Not
    calling it short-circuits the field, which is how an authorization extension refuses without
    ever running the resolver.

    Define `resolve` or `resolve_async` (or both -- `resolve_async` wins). Unlike Strawberry there
    is no sync/async mixing restriction and no `SyncToAsyncExtension`: bramble has exactly one
    execution path and it is async, so whichever method exists is called and awaited if awaitable.
    """

    def apply(self, field: "Field") -> None:
        """Called once at schema-build time, for an extension that wants to inspect or adjust the
        field's configuration.
        """

    def resolve(self, next_: Callable[..., Any], source: Any, info: "Info", **kwargs: Any) -> Any:
        raise NotImplementedError

    async def resolve_async(self, next_: Callable[..., Any], source: Any, info: "Info", **kwargs: Any) -> Any:
        raise NotImplementedError

    def map_arguments(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        """Reshapes the resolver's arguments after coercion, before the chain runs."""
        return kwargs

    @property
    def _supports_sync(self) -> bool:
        return type(self).resolve is not FieldExtension.resolve

    @property
    def _supports_async(self) -> bool:
        return type(self).resolve_async is not FieldExtension.resolve_async

    @property
    def _has_resolver(self) -> bool:
        """Whether this extension takes part in the resolve chain at all. One that only implements
        `apply`/`map_arguments` is left out rather than wrapping every call in a no-op.
        """
        return self._supports_sync or self._supports_async


# --- Driving the hooks ----------------------------------------------------------------------------


def _as_async_context_manager(extension: SchemaExtension, hook_name: str, *args: Any) -> Any:
    """Turns one hook into an async context manager, or returns `None` if the extension doesn't
    override it.

    A sync generator hook is wrapped with `contextlib.contextmanager` and adapted; an async
    generator with `asynccontextmanager`. Skipping non-overridden hooks matters: entering a
    context manager per extension per step, for hooks nobody implemented, is pure overhead on
    every request.
    """
    hook = getattr(type(extension), hook_name, None)
    if hook is None or hook is getattr(SchemaExtension, hook_name):
        return None

    bound = types.MethodType(hook, extension)

    if inspect.isasyncgenfunction(hook):
        return contextlib.asynccontextmanager(bound)(*args)

    if inspect.isgeneratorfunction(hook):
        sync_manager = contextlib.contextmanager(bound)(*args)

        @contextlib.asynccontextmanager
        async def adapted() -> AsyncIterator[None]:
            # Entering/exiting a sync context manager from the async path is fine: the hook body
            # is user code that chose to be sync, and forcing it onto a thread would change the
            # ordering guarantees the onion nesting depends on.
            with sync_manager:
                yield

        return adapted()

    raise TypeError(f"{type(extension).__name__}.{hook_name} must be a generator or async generator")


class ExtensionRunner:
    """Drives one request's `SchemaExtension` instances.

    Constructed per request, since a class-registered extension is instantiated per request (see
    `SchemaExtension`). `hook(...)` returns an async context manager entering every extension's
    hook in list order, so the first extension ends up outermost.
    """

    __slots__ = ("execution_context", "extensions")

    def __init__(self, extensions: Sequence[Any], execution_context: ExecutionContext) -> None:
        self.execution_context = execution_context
        self.extensions: list[SchemaExtension] = []
        for extension in extensions:
            # A class is instantiated per request; an instance is used as given.
            instance = extension(execution_context=execution_context) if isinstance(extension, type) else extension
            instance.execution_context = execution_context
            self.extensions.append(instance)

    @contextlib.asynccontextmanager
    async def hook(self, hook_name: str, *args: Any) -> AsyncIterator[None]:
        """Enters every extension's `hook_name` in list order and exits them in reverse.

        `AsyncExitStack` is what guarantees the error behaviour: if the wrapped body raises, or an
        inner hook raises on the way in, every already-entered hook still gets its "after" phase
        before the exception propagates. Same guarantee, and same mechanism, as `DependencyScope`
        teardown and subscription generator cleanup elsewhere in the codebase.
        """
        async with contextlib.AsyncExitStack() as stack:
            for extension in self.extensions:
                manager = _as_async_context_manager(extension, hook_name, *args)
                if manager is not None:
                    await stack.enter_async_context(manager)
            yield

    @property
    def resolve_extensions(self) -> list[SchemaExtension]:
        """The extensions that override `resolve`, in list order. Empty for the common case, which
        lets field execution skip the wrapping entirely.
        """
        return [extension for extension in self.extensions if type(extension)._implements_resolve()]

    async def get_results(self) -> dict[str, Any]:
        """Every extension's `get_results()`, merged with anything written to
        `execution_context.extensions_results`.
        """
        results: dict[str, Any] = {}
        for extension in self.extensions:
            value = extension.get_results()
            if inspect.isawaitable(value):
                value = await value
            if value:
                results.update(value)
        results.update(self.execution_context.extensions_results)
        return results


def build_field_resolver(resolver: Callable[..., Any], extensions: Sequence[FieldExtension]) -> Callable[..., Any]:
    """Composes a field's extensions around its resolver, once, at schema-build time.

    Returns a callable taking `(source, info, **kwargs)`. Extensions compose in list order with the
    first outermost, so `extensions=[A, B]` gives `A(B(resolver))`.

    Built once per field rather than per request: the chain depends only on the field's declared
    extensions, and rebuilding it on every resolution would be pure waste. Extensions that
    implement neither `resolve` nor `resolve_async` are omitted from the chain entirely.
    """

    async def call_resolver(source: Any, info: "Info", **kwargs: Any) -> Any:
        result = resolver(source, info, **kwargs)
        if inspect.isawaitable(result):
            result = await result
        return result

    chain: Callable[..., Any] = call_resolver
    # Reversed so the *first* listed extension ends up outermost.
    for extension in reversed([extension for extension in extensions if extension._has_resolver]):
        chain = _wrap_one(extension, chain)
    return chain


def _wrap_one(extension: FieldExtension, next_: Callable[..., Any]) -> Callable[..., Any]:
    # Bound as a default argument rather than captured, so each closure keeps its own pair even
    # though the loop above rebinds both names -- the codebase's standing convention for closures.
    async def wrapped(
        source: Any,
        info: "Info",
        _extension: FieldExtension = extension,
        _next: Callable[..., Any] = next_,
        **kwargs: Any,
    ) -> Any:
        method = _extension.resolve_async if _extension._supports_async else _extension.resolve
        result = method(_next, source, info, **kwargs)
        if inspect.isawaitable(result):
            result = await result
        return result

    return wrapped


__all__ = [
    "ExecutionContext",
    "ExtensionRunner",
    "FieldExtension",
    "SchemaExtension",
    "build_field_resolver",
]
