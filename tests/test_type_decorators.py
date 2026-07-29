from __future__ import annotations

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
    assert "Author" in (author_field.type_repr or "")


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
            def computed(self) -> int:
                return 1


def test_mutation_behaves_like_field() -> None:
    @bramble.type
    class Query:
        @bramble.field
        def hello(self) -> str:
            return "hi"

    @bramble.type
    class Mutation:
        @bramble.mutation
        def create_hello(self) -> str:
            return "hi"

    query_field = Query.__bramble_type_info__.fields[0]
    mutation_field = Mutation.__bramble_type_info__.fields[0]

    assert query_field.type_repr == mutation_field.type_repr
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
        def farewell(self) -> str:
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
