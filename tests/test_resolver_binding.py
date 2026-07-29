from __future__ import annotations

from typing import Annotated

import pytest

import bramble


def test_parent_and_info_and_argument_bind_correctly() -> None:
    @bramble.interface
    class Shape:
        radius: float

    @bramble.type
    class Circle(Shape):
        color: str

        @bramble.field
        def area(
            parent: bramble.Parent[Circle],
            info: bramble.Info,
            precision: Annotated[int, bramble.argument(name="digits", description="rounding precision")] = 2,
        ) -> float:
            return round(3.14159 * parent.radius**2, precision)

    field_info = Circle.__bramble_type_info__.fields[-1]
    assert field_info.name == "area"
    assert field_info.parent_parameter == "parent"
    assert field_info.info_parameter == "info"

    assert len(field_info.arguments) == 1
    argument = field_info.arguments[0]
    assert argument.name == "precision"
    assert argument.graphql_name == "digits"
    assert argument.description == "rounding precision"
    assert argument.has_default is True
    assert argument.is_nullable is False


def test_parent_only_resolver() -> None:
    @bramble.type
    class Circle:
        radius: float

        @bramble.field
        def diameter(parent: bramble.Parent[Circle]) -> float:
            return parent.radius * 2

    field_info = Circle.__bramble_type_info__.fields[-1]
    assert field_info.parent_parameter == "parent"
    assert field_info.info_parameter is None
    assert field_info.arguments == []


def test_info_only_resolver() -> None:
    @bramble.type
    class Query:
        @bramble.field
        def whoami(info: bramble.Info) -> str:
            return info.field_name

    field_info = Query.__bramble_type_info__.fields[0]
    assert field_info.parent_parameter is None
    assert field_info.info_parameter == "info"


def test_resolver_with_no_special_parameters_has_no_bindings() -> None:
    @bramble.type
    class Query:
        @bramble.field
        def version() -> str:
            return "1.0"

    field_info = Query.__bramble_type_info__.fields[0]
    assert field_info.parent_parameter is None
    assert field_info.info_parameter is None
    assert field_info.arguments == []


def test_argument_without_annotated_metadata() -> None:
    @bramble.type
    class Query:
        @bramble.field
        def greet(name: str) -> str:
            return f"hello {name}"

    argument = Query.__bramble_type_info__.fields[0].arguments[0]
    assert argument.name == "name"
    assert argument.graphql_name is None
    assert argument.description is None
    assert argument.is_nullable is False
    assert argument.has_default is False


def test_argument_nullable_via_union_none() -> None:
    @bramble.type
    class Query:
        @bramble.field
        def greet(name: str | None = None) -> str:
            return name or "hi"

    argument = Query.__bramble_type_info__.fields[0].arguments[0]
    assert argument.is_nullable is True
    assert argument.has_default is True


def test_argument_required_when_no_default_and_not_nullable() -> None:
    @bramble.type
    class Query:
        @bramble.field
        def greet(name: str) -> str:
            return name

    argument = Query.__bramble_type_info__.fields[0].arguments[0]
    assert argument.is_nullable is False
    assert argument.has_default is False


def test_argument_default_value_regardless_of_nullability() -> None:
    @bramble.type
    class Query:
        @bramble.field
        def greet(name: str = "world") -> str:
            return f"hello {name}"

    argument = Query.__bramble_type_info__.fields[0].arguments[0]
    assert argument.is_nullable is False
    assert argument.has_default is True


def test_untyped_self_parameter_raises_clear_error() -> None:
    with pytest.raises(bramble.SchemaError, match="Parent\\[T\\]"):

        @bramble.type
        class Broken:
            @bramble.field
            def broken(self) -> int:
                return 1


def test_untyped_root_parameter_raises_clear_error() -> None:
    with pytest.raises(bramble.SchemaError, match="Parent\\[T\\]"):

        @bramble.type
        class Broken:
            @bramble.field
            def broken(root) -> int:
                return 1


def test_untyped_other_parameter_raises_error() -> None:
    with pytest.raises(bramble.SchemaError):

        @bramble.type
        class Broken:
            @bramble.field
            def broken(whatever) -> int:
                return 1


def test_multiple_parent_parameters_rejected() -> None:
    with pytest.raises(bramble.SchemaError):

        @bramble.type
        class Broken:
            @bramble.field
            def broken(a: bramble.Parent[Broken], b: bramble.Parent[Broken]) -> int:
                return 1


def test_multiple_info_parameters_rejected() -> None:
    with pytest.raises(bramble.SchemaError):

        @bramble.type
        class Broken:
            @bramble.field
            def broken(a: bramble.Info, b: bramble.Info) -> int:
                return 1


def test_resolvers_called_unbound_still_work_via_normal_attribute_access() -> None:
    """Parameter *names* don't matter for classification, but Python's own descriptor-based
    instance-attribute access still binds positionally -- so calling a restored resolver the
    normal way (`instance.method()`) continues to work even though the parameter is now named
    `parent`/annotated `Parent[T]` rather than a conventional `self`.
    """

    @bramble.type
    class Circle:
        radius: float

        @bramble.field
        def area(parent: bramble.Parent[Circle]) -> float:
            return round(3.14159 * parent.radius**2, 2)

    circle = Circle(radius=2.0)
    assert circle.area() == pytest.approx(12.57, abs=1e-2)
