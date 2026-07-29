from __future__ import annotations

import pytest

import bramble
from bramble.directive import DirectiveLocation, DirectiveValue, apply_directive


def test_turn_uppercase_example_from_spec() -> None:
    @bramble.directive(locations=[DirectiveLocation.FIELD], description="Make string uppercase")
    def turn_uppercase(value: DirectiveValue[str]) -> str:
        return value.upper()

    info = turn_uppercase.__bramble_directive_info__
    assert info.name == "turnUppercase"
    assert info.description == "Make string uppercase"
    assert info.value_parameter == "value"
    assert info.arguments == []

    assert apply_directive(turn_uppercase, "hello") == "HELLO"


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

    result = apply_directive(replace, "JohnDoe", {"old": "John", "new": "Jane"})
    assert result == "JaneDoe"


def test_directives_can_be_chained() -> None:
    @bramble.directive(locations=[DirectiveLocation.FIELD])
    def replace(value: DirectiveValue[str], old: str, new: str) -> str:
        return value.replace(old, new)

    @bramble.directive(locations=[DirectiveLocation.FIELD])
    def turn_uppercase(value: DirectiveValue[str]) -> str:
        return value.upper()

    value = apply_directive(replace, "JohnDoe", {"old": "John", "new": "Jane"})
    value = apply_directive(turn_uppercase, value)

    assert value == "JANEDOE"


def test_directive_argument_with_default() -> None:
    @bramble.directive(locations=[DirectiveLocation.FIELD])
    def pad(value: DirectiveValue[str], width: int = 10) -> str:
        return value.rjust(width)

    info = pad.__bramble_directive_info__
    argument = next(a for a in info.arguments if a.name == "width")
    assert argument.has_default is True

    assert apply_directive(pad, "hi") == "hi".rjust(10)
    assert apply_directive(pad, "hi", {"width": 3}) == "hi".rjust(3)


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
        apply_directive(not_a_directive, "x")


def test_untyped_parameter_raises_schema_error() -> None:
    with pytest.raises(bramble.SchemaError):

        @bramble.directive(locations=[DirectiveLocation.FIELD])
        def broken(value):  # noqa: ANN001
            return value


def test_multiple_directive_value_parameters_rejected() -> None:
    with pytest.raises(bramble.SchemaError):

        @bramble.directive(locations=[DirectiveLocation.FIELD])
        def broken(a: DirectiveValue[str], b: DirectiveValue[str]) -> str:
            return a + b
