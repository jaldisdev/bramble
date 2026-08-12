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
from collections.abc import Callable
from typing import Any, NewType

import pytest

import bramble
from bramble._dependency import DependencyScope
from bramble._resolver import Info
from bramble.directive import DirectiveLocation, DirectiveValue, apply_directive
from bramble.schema.config import SchemaConfig

# `typing.get_type_hints` can't see an enclosing test function's local scope, so a NewType used
# only as a directive argument's annotation must live at module level (same gotcha elsewhere).
Slug = NewType("Slug", str)


def _apply(
    directive_function: Callable[..., Any], value: Any, arguments: dict[str, Any] | None = None
) -> Any:
    """`apply_directive` itself is async (§3c: it may need to resolve `Info`/`Depends[T]`
    parameters) -- this wraps it with a throwaway `Info`/`DependencyScope` pair for tests that only
    care about `DirectiveValue`/argument binding, not execution context or dependency injection.
    """
    return asyncio.run(apply_directive(directive_function, value, arguments, info=Info(), scope=DependencyScope()))


@bramble.input
class RepeatOptions:
    times: int


def test_turn_uppercase_example_from_spec() -> None:
    @bramble.directive(locations=[DirectiveLocation.FIELD], description="Make string uppercase")
    def turn_uppercase(value: DirectiveValue[str]) -> str:
        return value.upper()

    info = turn_uppercase.__bramble_directive_info__
    assert info.name == "turnUppercase"
    assert info.description == "Make string uppercase"
    assert info.value_parameter == "value"
    assert info.arguments == []

    assert _apply(turn_uppercase, "hello") == "HELLO"


def test_directive_name_defaults_to_camel_case_of_function_name() -> None:
    @bramble.directive(locations=[DirectiveLocation.FIELD])
    def turn_uppercase(value: DirectiveValue[str]) -> str:
        return value.upper()

    assert turn_uppercase.__bramble_directive_info__.name == "turnUppercase"


def test_directive_name_can_be_overridden() -> None:
    @bramble.directive(locations=[DirectiveLocation.FIELD], name="shout")
    def turn_uppercase(value: DirectiveValue[str]) -> str:
        return value.upper()

    assert turn_uppercase.__bramble_directive_info__.name == "shout"


def test_directive_with_arguments_binds_and_applies_correctly() -> None:
    @bramble.directive(locations=[DirectiveLocation.FIELD])
    def replace(value: DirectiveValue[str], old: str, new: str) -> str:
        return value.replace(old, new)

    info = replace.__bramble_directive_info__
    assert info.value_parameter == "value"
    argument_names = {argument.name for argument in info.arguments}
    assert argument_names == {"old", "new"}

    result = _apply(replace, "JohnDoe", {"old": "John", "new": "Jane"})
    assert result == "JaneDoe"


def test_directives_can_be_chained() -> None:
    @bramble.directive(locations=[DirectiveLocation.FIELD])
    def replace(value: DirectiveValue[str], old: str, new: str) -> str:
        return value.replace(old, new)

    @bramble.directive(locations=[DirectiveLocation.FIELD])
    def turn_uppercase(value: DirectiveValue[str]) -> str:
        return value.upper()

    value = _apply(replace, "JohnDoe", {"old": "John", "new": "Jane"})
    value = _apply(turn_uppercase, value)

    assert value == "JANEDOE"


def test_directive_argument_with_default() -> None:
    @bramble.directive(locations=[DirectiveLocation.FIELD])
    def pad(value: DirectiveValue[str], width: int = 10) -> str:
        return value.rjust(width)

    info = pad.__bramble_directive_info__
    argument = next(a for a in info.arguments if a.name == "width")
    assert argument.has_default is True

    assert _apply(pad, "hi") == "hi".rjust(10)
    assert _apply(pad, "hi", {"width": 3}) == "hi".rjust(3)


def test_directive_without_directive_value_parameter() -> None:
    @bramble.directive(locations=[DirectiveLocation.FIELD])
    def constant(value: str) -> str:
        return value

    info = constant.__bramble_directive_info__
    assert info.value_parameter is None
    assert {a.name for a in info.arguments} == {"value"}


def test_applying_non_directive_function_raises_schema_error() -> None:
    def not_a_directive(value: str) -> str:
        return value

    with pytest.raises(bramble.SchemaError):
        _apply(not_a_directive, "x")


def test_untyped_parameter_raises_schema_error() -> None:
    with pytest.raises(bramble.SchemaError):

        @bramble.directive(locations=[DirectiveLocation.FIELD])
        def broken(value):
            return value


def test_multiple_directive_value_parameters_rejected() -> None:
    with pytest.raises(bramble.SchemaError):

        @bramble.directive(locations=[DirectiveLocation.FIELD])
        def broken(a: DirectiveValue[str], b: DirectiveValue[str]) -> str:
            return a + b


# Async directives, and directives whose own arguments are typed as a custom scalar or a
# `@bramble.input` type -- the latter is a direct regression test for a bug this session found and
# fixed (a directive's own input-typed argument used to arrive as a raw dict, never coerced into a
# real instance, because the schema graph walker never discovered a type reachable only via a
# directive's own argument annotations).


def test_runs_async_directive() -> None:
    @bramble.directive(locations=[DirectiveLocation.FIELD])
    async def shout_async(value: DirectiveValue[str]) -> str:
        return value.upper()

    @bramble.type
    class Query:
        @bramble.field
        def greeting() -> str:
            return "hello"

    schema = bramble.Schema(query=Query, directives=[shout_async])

    assert schema.execute("{ greeting @shoutAsync }") == {"data": {"greeting": "HELLO"}}


def test_directive_argument_typed_as_custom_scalar() -> None:
    @bramble.directive(locations=[DirectiveLocation.FIELD])
    def append_slug(value: DirectiveValue[str], slug: Slug) -> str:
        return f"{value}-{slug}"

    @bramble.type
    class Query:
        @bramble.field
        def greeting() -> str:
            return "hi"

    schema = bramble.Schema(
        query=Query,
        directives=[append_slug],
        config=SchemaConfig(
            scalar_map={Slug: bramble.scalar(name="Slug", parse_value=lambda v: v.lower().replace(" ", "-"))}
        ),
    )

    result = schema.execute('{ greeting @appendSlug(slug: "Hello World") }')
    assert result == {"data": {"greeting": "hi-hello-world"}}


def test_directive_argument_typed_as_input_object() -> None:
    @bramble.directive(locations=[DirectiveLocation.FIELD])
    def repeat(value: DirectiveValue[str], options: RepeatOptions) -> str:
        return value * options.times

    @bramble.type
    class Query:
        @bramble.field
        def greeting() -> str:
            return "hi"

    schema = bramble.Schema(query=Query, directives=[repeat])

    result = schema.execute("{ greeting @repeat(options: {times: 2}) }")
    assert result == {"data": {"greeting": "hihi"}}
    assert schema.types_by_name["RepeatOptions"] is RepeatOptions
