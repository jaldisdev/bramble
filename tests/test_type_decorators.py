from __future__ import annotations

import dataclasses

import pytest

import bramble


def test_nested_types() -> None:
    @bramble.type
    class Author:
        name: str

    @bramble.type
    class Post:
        title: str
        author: Author

    info = Post.__bramble_type_info__
    assert [f.name for f in info.fields] == ["title", "author"]
    author_field = next(f for f in info.fields if f.name == "author")
    assert author_field.graphql_type == "Author!"


def test_interface_inheritance_chain() -> None:
    @bramble.interface
    class Error:
        message: str

    @bramble.interface
    class FieldError(Error):
        field: str

    @bramble.type
    class PasswordTooShort(FieldError):
        min_length: int

    info = PasswordTooShort.__bramble_type_info__
    assert info.kind == "type"
    assert {f.name for f in info.fields} == {"message", "field", "min_length"}


def test_input_with_one_of() -> None:
    @bramble.input(one_of=True)
    class Filter:
        by_id: int | None = None
        by_name: str | None = None

    info = Filter.__bramble_type_info__
    assert info.kind == "input"
    assert info.one_of is True
    assert {f.name for f in info.fields} == {"by_id", "by_name"}


def test_input_without_one_of_defaults_false() -> None:
    @bramble.input
    class Simple:
        value: int

    assert Simple.__bramble_type_info__.one_of is False


def test_input_with_resolver_field_fails_to_build() -> None:
    with pytest.raises(bramble.SchemaError):

        @bramble.input
        class BadInput:
            @bramble.field
            def computed() -> int:
                return 1


def test_mutation_behaves_like_field() -> None:
    @bramble.type
    class Query:
        @bramble.field
        def hello() -> str:
            return "hi"

    @bramble.type
    class Mutation:
        @bramble.mutation
        def create_hello() -> str:
            return "hi"

    query_field = Query.__bramble_type_info__.fields[0]
    mutation_field = Mutation.__bramble_type_info__.fields[0]

    assert query_field.graphql_type == mutation_field.graphql_type == "String!"
    assert query_field.has_resolver and mutation_field.has_resolver


def test_type_interface_input_share_process_type_code_path() -> None:
    @bramble.type(name="Named")
    class A:
        value: int

    @bramble.interface(name="Named")
    class B:
        value: int

    @bramble.input(name="Named")
    class C:
        value: int

    kinds = {A.__bramble_type_info__.kind, B.__bramble_type_info__.kind, C.__bramble_type_info__.kind}
    assert kinds == {"type", "interface", "input"}
    for cls in (A, B, C):
        info = cls.__bramble_type_info__
        assert info.name == "Named"
        assert [f.name for f in info.fields] == ["value"]


def test_field_supports_both_method_and_plain_function_resolver_syntax() -> None:
    def resolve_greeting() -> str:
        return "hi"

    @bramble.type
    class Query:
        greeting: str = bramble.field(resolver=resolve_greeting)

        @bramble.field
        def farewell() -> str:
            return "bye"

    fields_by_name = {f.name: f for f in Query.__bramble_type_info__.fields}
    assert fields_by_name["greeting"].has_resolver is True
    assert fields_by_name["farewell"].has_resolver is True


def test_bare_and_parameterized_type_decorator() -> None:
    @bramble.type
    class Bare:
        value: int

    @bramble.type(name="Custom", description="a custom type")
    class Parameterized:
        value: int

    assert Bare.__bramble_type_info__.name == "Bare"
    assert Parameterized.__bramble_type_info__.name == "Custom"
    assert Parameterized.__bramble_type_info__.description == "a custom type"


def test_decorated_types_are_real_dataclasses() -> None:
    @bramble.interface
    class Shape:
        radius: float

    @bramble.type
    class Circle(Shape):
        color: str

        @bramble.field
        def area(parent: bramble.Parent[Circle]) -> float:
            return 3.14159 * parent.radius**2

    assert dataclasses.is_dataclass(Shape)
    assert dataclasses.is_dataclass(Circle)

    circle = Circle(radius=2.0, color="red")
    assert circle.radius == 2.0
    assert circle.color == "red"
    assert circle.area() == pytest.approx(12.566, abs=1e-3)

    # kw_only=True: no positional construction, even across inheritance
    with pytest.raises(TypeError):
        Circle(2.0, "red")  # type: ignore[call-arg]


def test_input_types_are_constructible() -> None:
    @bramble.input(one_of=True)
    class Filter:
        by_id: int | None = None
        by_name: str | None = None

    filter_by_id = Filter(by_id=5)
    assert filter_by_id.by_id == 5
    assert filter_by_id.by_name is None


def test_resolver_fields_excluded_from_repr_and_eq() -> None:
    @bramble.type
    class Circle:
        radius: float

        @bramble.field
        def area(parent: bramble.Parent[Circle]) -> float:
            return 3.14159 * parent.radius**2

    first = Circle(radius=2.0)
    second = Circle(radius=2.0)

    assert first == second
    assert "area" not in repr(first)
    assert "radius=2.0" in repr(first)


def test_resolver_restored_as_callable_after_dataclass_processing() -> None:
    @bramble.type
    class Circle:
        radius: float

        @bramble.field
        def area(parent: bramble.Parent[Circle]) -> float:
            return 3.14159 * parent.radius**2

    # dataclasses.dataclass() strips a resolver-backed field's raw class attribute
    # (it has no usable default); bramble must restore it so the method still works.
    assert callable(Circle.area)
    assert Circle(radius=1.0).area() == pytest.approx(3.14159, abs=1e-5)


def test_method_style_field_without_return_annotation_fails_to_build() -> None:
    with pytest.raises(bramble.SchemaError):

        @bramble.type
        class Broken:
            @bramble.field
            def broken(self):
                return 1


def test_field_default_and_default_factory() -> None:
    @bramble.type
    class Config:
        timeout: int = bramble.field(description="request timeout", default=30)
        tags: list = bramble.field(default_factory=list)

    default_config = Config()
    assert default_config.timeout == 30
    assert default_config.tags == []

    overridden = Config(timeout=60, tags=["a"])
    assert overridden.timeout == 60
    assert overridden.tags == ["a"]


def test_field_default_and_default_factory_are_mutually_exclusive() -> None:
    with pytest.raises(bramble.SchemaError):
        bramble.field(default=1, default_factory=list)


def test_field_resolver_and_default_are_mutually_exclusive() -> None:
    with pytest.raises(bramble.SchemaError):
        bramble.field(resolver=lambda: 1, default=5)

    with pytest.raises(bramble.SchemaError):
        # a default set up front, then a resolver attached afterward via `@field(...)`
        # on a method -- the conflict must still be caught when applied out of order.
        deferred = bramble.field(default=5)
        deferred(lambda self: 1)
