from __future__ import annotations

import dataclasses

import pytest

import bramble

# `typing.get_type_hints` can't see an enclosing test function's local scope (only module
# globals), so any class referenced *from another annotation* (a field type, a resolver's own
# argument type) has to live at module level here -- matches the rest of the test suite's own
# established convention for this gotcha.


@bramble.type
class _UserWithPrivateAge:
    name: str
    age: bramble.Private[int]

    @bramble.field
    def is_adult(parent: bramble.Parent[object]) -> bool:
        return parent.age >= 18  # type: ignore[attr-defined]


@bramble.type
class _Internal:
    x: int


@bramble.input
class _FilterWithPrivateFlag:
    term: str
    # A private input field the client can never supply needs a default, or the dataclass
    # constructor `_coerce_value` calls (only ever passing the client-supplied fields) would fail.
    internal_flag: bramble.Private[bool] = False


def test_private_field_is_excluded_from_sdl_and_schema() -> None:
    @bramble.type
    class User:
        name: str
        age: bramble.Private[int]

    schema = bramble.Schema(query=User)
    sdl = schema.to_sdl()

    assert "age" not in sdl
    assert "name: String!" in sdl


def test_querying_a_private_field_fails_validation() -> None:
    @bramble.type
    class Query:
        @bramble.field
        def user() -> _UserWithPrivateAge:
            return _UserWithPrivateAge(name="Ada", age=30)

    schema = bramble.Schema(query=Query, types=[_UserWithPrivateAge])

    with pytest.raises(bramble.GraphQLError, match="age"):
        schema.execute("{ user { name age } }")


def test_resolver_can_still_read_a_private_field_off_parent() -> None:
    @bramble.type
    class Query:
        @bramble.field
        def user() -> _UserWithPrivateAge:
            return _UserWithPrivateAge(name="Ada", age=17)

    schema = bramble.Schema(query=Query, types=[_UserWithPrivateAge])

    assert schema.execute("{ user { isAdult } }") == {"data": {"user": {"isAdult": False}}}


def test_private_field_remains_a_normal_dataclass_attribute() -> None:
    @bramble.type
    class User:
        name: str
        age: bramble.Private[int]

    first = User(name="Ada", age=30)
    second = User(name="Ada", age=30)

    assert first == second
    assert first.age == 30
    assert dataclasses.asdict(first) == {"name": "Ada", "age": 30}


def test_private_plus_bramble_field_raises_schema_error() -> None:
    with pytest.raises(bramble.SchemaError, match="cannot be both Private and a bramble.field"):

        @bramble.type
        class Bad:
            secret: bramble.Private[str] = bramble.field(description="oops")


def test_private_plus_bare_bramble_field_raises_schema_error() -> None:
    # Matches even a bare `bramble.field()` with no extra configuration -- the conflict is about
    # the field having been explicitly constructed at all, not about which options were passed.
    with pytest.raises(bramble.SchemaError, match="cannot be both Private"):

        @bramble.type
        class Bad:
            secret: bramble.Private[str] = bramble.field()


def test_private_fields_own_type_is_not_registered_into_the_schema() -> None:
    @bramble.type
    class Query:
        name: str
        hidden: bramble.Private[_Internal]

    schema = bramble.Schema(query=Query)

    assert "Internal" not in schema.types_by_name
    assert "Internal" not in schema.to_sdl()


def test_private_field_on_input_type_is_excluded() -> None:
    @bramble.type
    class Query:
        @bramble.field
        def search(filter: _FilterWithPrivateFlag) -> str:
            return filter.term

    schema = bramble.Schema(query=Query)
    sdl = schema.to_sdl()

    assert "internal_flag" not in sdl
    assert "term: String!" in sdl
    assert schema.execute('{ search(filter: {term: "hi"}) }') == {"data": {"search": "hi"}}
