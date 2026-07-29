from __future__ import annotations

from typing import Annotated, NewType

import pytest

import bramble
from bramble.schema.config import SchemaConfig
from bramble.schema_directive import Location

# `typing.get_type_hints` (used to resolve a resolver parameter's `Annotated[...]` annotation, and
# a resolver's own return annotation) can only see module globals, never an enclosing test
# function's locals -- so anything referenced *from an annotation* (as opposed to a plain
# decorator call-time argument, like `directives=[SomeMarker()]` on a type/field) has to live at
# module level here, matching test_schema.py's/test_sdl.py's own established convention.


@bramble.schema_directive(locations=[Location.ARGUMENT_DEFINITION])
class Sensitive:
    reason: str


@bramble.schema_directive(locations=[Location.OBJECT])
class _ObjectOnlyForAnnotatedTests:
    pass


Base64 = NewType("Base64", bytes)


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


def test_argument_level_directive_at_argument_definition_location_succeeds() -> None:
    @bramble.type
    class Query:
        @bramble.field
        def greet(name: Annotated[str, bramble.argument(directives=[Sensitive(reason="pii")])]) -> str:
            return f"hi {name}"

    schema = bramble.Schema(query=Query)
    assert schema.execute('query { greet(name: "Ada") }') == {"data": {"greet": "hi Ada"}}
    assert "greet(name: String! @sensitive(reason: \"pii\")): String!" in schema.to_sdl()


def test_argument_level_directive_at_disallowed_location_fails_with_clear_error() -> None:
    with pytest.raises(bramble.SchemaError, match="ARGUMENT_DEFINITION"):

        @bramble.type
        class Query:
            @bramble.field
            def greet(
                name: Annotated[str, bramble.argument(directives=[_ObjectOnlyForAnnotatedTests()])],
            ) -> str:
                return name


def test_non_directive_objects_in_argument_directives_are_ignored() -> None:
    @bramble.type
    class Query:
        @bramble.field
        def greet(name: Annotated[str, bramble.argument(directives=[object()])]) -> str:
            return name

    schema = bramble.Schema(query=Query)
    assert schema.execute('query { greet(name: "Ada") }') == {"data": {"greet": "Ada"}}


def test_scalar_level_directive_at_scalar_location_succeeds() -> None:
    @bramble.schema_directive(locations=[Location.SCALAR])
    class SpecifiedBy:
        url: str

    @bramble.type
    class Query:
        @bramble.field
        def data() -> Base64:
            return b"hi"

    config = SchemaConfig(
        scalar_map={Base64: bramble.scalar(name="Base64", directives=[SpecifiedBy(url="https://example.com")])}
    )
    schema = bramble.Schema(query=Query, config=config)

    assert 'scalar Base64 @specifiedBy(url: "https://example.com")' in schema.to_sdl()


def test_scalar_level_directive_at_disallowed_location_fails_with_clear_error() -> None:
    @bramble.type
    class Query:
        @bramble.field
        def data() -> Base64:
            return b"hi"

    config = SchemaConfig(
        scalar_map={Base64: bramble.scalar(name="Base64", directives=[_ObjectOnlyForAnnotatedTests()])}
    )

    with pytest.raises(bramble.SchemaError, match="SCALAR"):
        bramble.Schema(query=Query, config=config)


def test_non_directive_objects_in_scalar_directives_are_ignored() -> None:
    @bramble.type
    class Query:
        @bramble.field
        def data() -> Base64:
            return b"hi"

    config = SchemaConfig(scalar_map={Base64: bramble.scalar(name="Base64", directives=[object()])})
    schema = bramble.Schema(query=Query, config=config)

    assert schema.execute("query { data }") == {"data": {"data": b"hi"}}
