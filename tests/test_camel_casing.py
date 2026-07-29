from __future__ import annotations

import bramble
from bramble.schema.config import SchemaConfig

# Exercises `auto_camel_case` (SchemaConfig, Task 84): the default camelCase naming convention
# for fields/arguments, and disabling it to keep raw snake_case identifiers instead.


def test_camel_case_is_on_by_default() -> None:
    @bramble.type
    class Query:
        @bramble.field
        def get_user(user_id: int) -> str:
            return f"user-{user_id}"

    schema = bramble.Schema(query=Query)

    assert "getUser(userId: Int!): String!" in schema.to_sdl()
    result = schema.execute("{ getUser(userId: 1) }")
    assert result == {"data": {"getUser": "user-1"}}


def test_can_set_camel_casing_explicitly() -> None:
    @bramble.type
    class Query:
        @bramble.field
        def get_user(user_id: int) -> str:
            return f"user-{user_id}"

    schema = bramble.Schema(query=Query, config=SchemaConfig(auto_camel_case=True))

    assert "getUser(userId: Int!): String!" in schema.to_sdl()


def test_can_set_camel_casing_to_false() -> None:
    @bramble.type
    class Query:
        @bramble.field
        def get_user(user_id: int) -> str:
            return f"user-{user_id}"

    schema = bramble.Schema(query=Query, config=SchemaConfig(auto_camel_case=False))

    assert "get_user(user_id: Int!): String!" in schema.to_sdl()
    result = schema.execute("{ get_user(user_id: 1) }")
    assert result == {"data": {"get_user": "user-1"}}


def test_can_set_camel_casing_to_false_uses_explicit_name_override() -> None:
    @bramble.type
    class Query:
        @bramble.field(name="renamedField")
        def some_field() -> str:
            return "x"

    schema = bramble.Schema(query=Query, config=SchemaConfig(auto_camel_case=False))

    assert "renamedField: String!" in schema.to_sdl()
    assert "some_field" not in schema.to_sdl()


def test_camel_case_is_on_by_default_for_arguments() -> None:
    @bramble.type
    class Query:
        @bramble.field
        def echo(input_value: str) -> str:
            return input_value

    schema = bramble.Schema(query=Query)

    assert "echo(inputValue: String!): String!" in schema.to_sdl()


def test_can_turn_camel_case_off_for_arguments() -> None:
    @bramble.type
    class Query:
        @bramble.field
        def echo(input_value: str) -> str:
            return input_value

    schema = bramble.Schema(query=Query, config=SchemaConfig(auto_camel_case=False))

    assert "echo(input_value: String!): String!" in schema.to_sdl()


def test_can_turn_camel_case_off_for_arguments_execution_works() -> None:
    @bramble.type
    class Query:
        @bramble.field
        def echo(input_value: str) -> str:
            return input_value

    schema = bramble.Schema(query=Query, config=SchemaConfig(auto_camel_case=False))

    result = schema.execute('{ echo(input_value: "hi") }')
    assert result == {"data": {"echo": "hi"}}
