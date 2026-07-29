from __future__ import annotations

import pytest

import bramble
from bramble.schema_directive import Location


def test_keys_directive_example_from_spec() -> None:
    @bramble.schema_directive(locations=[Location.OBJECT])
    class Keys:
        fields: str

    @bramble.type(directives=[Keys(fields="id")])
    class User:
        id: str
        name: str

    info = Keys.__bramble_directive_info__
    assert info.name == "keys"
    assert info.locations == ["OBJECT"]
    assert [(f.name, f.graphql_name) for f in info.fields] == [("fields", None)]

    keys_instance = Keys(fields="id")
    assert keys_instance.fields == "id"

    assert User(id="1", name="Ada").id == "1"


def test_directive_name_defaults_to_camel_case_of_class_name() -> None:
    @bramble.schema_directive(locations=[Location.OBJECT])
    class SomeDirective:
        pass

    assert SomeDirective.__bramble_directive_info__.name == "someDirective"


def test_directive_name_can_be_overridden() -> None:
    @bramble.schema_directive(locations=[Location.OBJECT], name="customDirective")
    class SomeDirective:
        pass

    assert SomeDirective.__bramble_directive_info__.name == "customDirective"


def test_directive_description() -> None:
    @bramble.schema_directive(locations=[Location.OBJECT], description="a custom directive")
    class SomeDirective:
        pass

    assert SomeDirective.__bramble_directive_info__.description == "a custom directive"


def test_directive_field_name_override() -> None:
    @bramble.schema_directive(locations=[Location.OBJECT])
    class MyDirective:
        fields: str = bramble.directive_field(name="as")

    info = MyDirective.__bramble_directive_info__
    assert [(f.name, f.graphql_name) for f in info.fields] == [("fields", "as")]

    instance = MyDirective(fields="x")
    assert instance.fields == "x"


def test_directive_applies_to_multiple_locations() -> None:
    @bramble.schema_directive(locations=[Location.OBJECT, Location.INTERFACE])
    class Multi:
        pass

    @bramble.type(directives=[Multi()])
    class SomeType:
        value: int

    @bramble.interface(directives=[Multi()])
    class SomeInterface:
        value: int


def test_directive_at_disallowed_location_fails_with_clear_error() -> None:
    @bramble.schema_directive(locations=[Location.FIELD_DEFINITION])
    class Deprecated:
        reason: str

    with pytest.raises(bramble.SchemaError, match="OBJECT"):

        @bramble.type(directives=[Deprecated(reason="nope")])
        class BadUser:
            id: str


def test_directive_on_interface_validated_against_interface_location() -> None:
    @bramble.schema_directive(locations=[Location.INTERFACE])
    class InterfaceOnly:
        pass

    @bramble.interface(directives=[InterfaceOnly()])
    class Node:
        id: str

    with pytest.raises(bramble.SchemaError):

        @bramble.type(directives=[InterfaceOnly()])
        class BadType:
            id: str


def test_directive_on_input_validated_against_input_object_location() -> None:
    @bramble.schema_directive(locations=[Location.INPUT_OBJECT])
    class InputOnly:
        pass

    @bramble.input(directives=[InputOnly()])
    class Filter:
        value: int

    with pytest.raises(bramble.SchemaError):

        @bramble.type(directives=[InputOnly()])
        class BadType:
            value: int


def test_non_directive_objects_in_directives_are_ignored() -> None:
    @bramble.type(directives=[object()])
    class Harmless:
        value: int

    assert Harmless(value=1).value == 1


def test_field_level_directive_at_field_definition_location_succeeds() -> None:
    @bramble.schema_directive(locations=[Location.FIELD_DEFINITION])
    class Deprecated:
        reason: str

    @bramble.type
    class Query:
        old_field: str = bramble.field(directives=[Deprecated(reason="use newField")], default="x")

    assert Query().old_field == "x"


def test_field_level_directive_at_disallowed_location_fails_with_clear_error() -> None:
    @bramble.schema_directive(locations=[Location.OBJECT])
    class ObjectOnly:
        pass

    with pytest.raises(bramble.SchemaError, match="FIELD_DEFINITION"):

        @bramble.type
        class Query:
            f: str = bramble.field(directives=[ObjectOnly()], default="x")


def test_non_directive_objects_in_field_directives_are_ignored() -> None:
    @bramble.type
    class Query:
        f: str = bramble.field(directives=[object()], default="x")

    assert Query().f == "x"
