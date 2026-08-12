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
import enum
from collections.abc import AsyncGenerator
from typing import Any

import bramble
from bramble.directive import DirectiveLocation, DirectiveValue
from bramble.schema.config import SchemaConfig

# `Info.field_directives` exists for the case an operation directive's own function cannot serve:
# reading what the query asked for *before* the field resolves. A directive function only ever sees
# the value a resolver already returned, so a directive carrying request context (`@auth(token:)`,
# `@inContext(language:)`) has to be readable from the resolver -- or from a `SchemaExtension.resolve`
# wrapping it -- while there is still a fetch to influence.


@bramble.input
class _Snapshot:
    at: str


@bramble.enum
class _Region(enum.Enum):
    EU = "EU"
    US = "US"


@bramble.directive(locations=[DirectiveLocation.FIELD])
def in_context(
    value: DirectiveValue[str],
    language: str,
    region: _Region | None = None,
    snapshot: _Snapshot | None = None,
) -> str:
    return value


@bramble.directive(locations=[DirectiveLocation.FIELD], name="auth")
def auth(value: DirectiveValue[Any], token: str) -> Any:
    return value


# A type referenced by another type's resolver return annotation has to be resolvable by
# `typing.get_type_hints`, which never sees an enclosing test function's locals -- hence module
# scope for this pair, unlike the single-type schemas above.
_NESTED_SEEN: dict[str, tuple[bramble.FieldDirective, ...]] = {}


@bramble.type
class _NestedAuthor:
    @bramble.field
    def name(info: bramble.Info) -> str:
        _NESTED_SEEN["name"] = info.field_directives
        return "Ada"


@bramble.type
class _NestedQuery:
    @bramble.field
    def author(info: bramble.Info) -> _NestedAuthor:
        _NESTED_SEEN["author"] = info.field_directives
        return _NestedAuthor()


def _directives_of(recorded: list[tuple[bramble.FieldDirective, ...]]) -> list[tuple[str, dict[str, Any]]]:
    return [(directive.name, directive.arguments) for occurrence in recorded for directive in occurrence]


def test_field_directives_are_readable_before_the_resolver_returns() -> None:
    seen: list[tuple[bramble.FieldDirective, ...]] = []

    @bramble.type
    class Query:
        @bramble.field
        def greet(info: bramble.Info) -> str:
            seen.append(info.field_directives)
            return "hello"

    schema = bramble.Schema(query=Query, directives=[in_context])

    assert schema.execute('{ greet @inContext(language: "de") }') == {"data": {"greet": "hello"}}
    assert _directives_of(seen) == [("inContext", {"language": "de"})]


def test_field_directives_is_empty_for_a_field_without_any() -> None:
    @bramble.type
    class Query:
        @bramble.field
        def greet(info: bramble.Info) -> str:
            assert info.field_directives == ()
            return "hello"

    schema = bramble.Schema(query=Query, directives=[in_context])

    assert schema.execute("{ greet }") == {"data": {"greet": "hello"}}


def test_field_directive_arguments_are_coerced_and_keyed_by_python_name() -> None:
    seen: list[tuple[bramble.FieldDirective, ...]] = []

    @bramble.type
    class Query:
        @bramble.field
        def greet(info: bramble.Info) -> str:
            seen.append(info.field_directives)
            return "hello"

    schema = bramble.Schema(query=Query, directives=[in_context])
    result = schema.execute(
        'query($region: _Region) { greet @inContext(language: "de", region: $region, snapshot: {at: "yesterday"}) }',
        variable_values={"region": "US"},
    )

    assert result == {"data": {"greet": "hello"}}
    (arguments,) = [directive.arguments for occurrence in seen for directive in occurrence]
    # An enum arrives as the Python member and an input object as a real instance of the declared
    # input class, exactly as the directive's own function would have received them.
    assert arguments == {"language": "de", "region": _Region.US, "snapshot": _Snapshot(at="yesterday")}


def test_several_directives_are_reported_in_source_order() -> None:
    seen: list[tuple[bramble.FieldDirective, ...]] = []

    @bramble.type
    class Query:
        @bramble.field
        def greet(info: bramble.Info) -> str:
            seen.append(info.field_directives)
            return "hello"

    schema = bramble.Schema(query=Query, directives=[in_context, auth])
    schema.execute('{ greet @auth(token: "t") @inContext(language: "de") }')

    assert _directives_of(seen) == [("auth", {"token": "t"}), ("inContext", {"language": "de"})]


def test_skip_and_include_never_appear_in_field_directives() -> None:
    seen: list[tuple[bramble.FieldDirective, ...]] = []

    @bramble.type
    class Query:
        @bramble.field
        def greet(info: bramble.Info) -> str:
            seen.append(info.field_directives)
            return "hello"

    schema = bramble.Schema(query=Query, directives=[in_context])
    result = schema.execute('{ greet @include(if: true) @inContext(language: "de") }')

    assert result == {"data": {"greet": "hello"}}
    assert _directives_of(seen) == [("inContext", {"language": "de"})]


def test_a_directive_the_schema_does_not_declare_is_reported_unmapped_when_validation_is_off() -> None:
    seen: list[tuple[bramble.FieldDirective, ...]] = []

    @bramble.type
    class Query:
        @bramble.field
        def greet(info: bramble.Info) -> str:
            seen.append(info.field_directives)
            return "hello"

    schema = bramble.Schema(query=Query, config=SchemaConfig(validate_queries=False))
    result = schema.execute('{ greet @inContext(language: "de") }')

    # There is no declaration to map parameter names or coerce against, so the arguments stay
    # exactly as written. Applying it still fails -- that is what the field error below is.
    assert _directives_of(seen) == [("inContext", {"language": "de"})]
    assert result["data"] is None
    assert "unknown operation directive '@inContext'" in result["errors"][0]["message"]


def test_a_nested_field_reports_its_own_directives_not_its_parents() -> None:
    _NESTED_SEEN.clear()
    schema = bramble.Schema(query=_NestedQuery, directives=[in_context, auth])

    schema.execute('{ author @auth(token: "t") { name @inContext(language: "de") } }')

    assert [(directive.name, directive.arguments) for directive in _NESTED_SEEN["author"]] == [
        ("auth", {"token": "t"})
    ]
    assert [(directive.name, directive.arguments) for directive in _NESTED_SEEN["name"]] == [
        ("inContext", {"language": "de"})
    ]


def test_a_subscription_event_sees_the_root_fields_directives() -> None:
    seen: list[tuple[bramble.FieldDirective, ...]] = []

    @bramble.type
    class Query:
        @bramble.field
        def greet() -> str:
            return "hello"

    @bramble.type
    class Subscription:
        @bramble.subscription
        async def counter(info: bramble.Info) -> AsyncGenerator[int, None]:
            seen.append(info.field_directives)
            yield 1

    schema = bramble.Schema(query=Query, subscription=Subscription, directives=[auth])

    async def scenario() -> list[dict[str, Any]]:
        return [
            response
            async for response in schema.subscribe_async('subscription { counter @auth(token: "t") }')
        ]

    assert asyncio.run(scenario()) == [{"data": {"counter": 1}}]
    assert _directives_of(seen) == [("auth", {"token": "t"})]


def test_a_directive_function_still_transforms_the_resolved_value() -> None:
    """`_apply_custom_directives` now consumes `Info.field_directives` rather than re-reading the
    lowered field, so the post-resolution behaviour has to keep working unchanged.
    """

    @bramble.directive(locations=[DirectiveLocation.FIELD])
    def shout(value: DirectiveValue[str], times: int = 1) -> str:
        return value.upper() + "!" * times

    @bramble.type
    class Query:
        @bramble.field
        def greet() -> str:
            return "hello"

    schema = bramble.Schema(query=Query, directives=[shout])

    assert schema.execute("{ greet @shout(times: 3) }") == {"data": {"greet": "HELLO!!!"}}


def test_a_schema_extension_can_read_the_directives_before_calling_the_resolver() -> None:
    """The shape jaldis' `FrontAccessExtension`/`FrontContextExtension` port to: write into the
    context from a `resolve` hook, using the directive arguments, before the field resolves.
    """

    class LanguageExtension(bramble.SchemaExtension):
        def resolve(self, next_, source, info: bramble.Info, **kwargs: Any) -> Any:
            for directive in info.field_directives:
                if directive.name == "inContext":
                    info.context["language"] = directive.arguments["language"]
            return next_(source, info, **kwargs)

    @bramble.type
    class Query:
        @bramble.field
        def greet(info: bramble.Info) -> str:
            return f"hello in {info.context.get('language', 'en')}"

    schema = bramble.Schema(query=Query, directives=[in_context], extensions=[LanguageExtension])
    context: dict[str, Any] = {}

    result = schema.execute('{ greet @inContext(language: "de") }', context=context)

    assert result == {"data": {"greet": "hello in de"}}
    assert context == {"language": "de"}
