from __future__ import annotations

import base64
from typing import NewType

import bramble
from bramble.schema.config import SchemaConfig

Base64 = NewType("Base64", bytes)


def test_scalar_definition_fields() -> None:
    def serialize(value: bytes) -> str:
        return base64.b64encode(value).decode("utf-8")

    def parse_value(value: str) -> bytes:
        return base64.b64decode(value)

    definition = bramble.scalar(
        name="Base64",
        description="Base64-encoded bytes",
        serialize=serialize,
        parse_value=parse_value,
    )

    assert definition.name == "Base64"
    assert definition.description == "Base64-encoded bytes"
    assert definition.specified_by_url is None
    assert definition.serialize is serialize
    assert definition.parse_value is parse_value
    assert definition.parse_literal is None
    assert definition.directives == ()


def test_scalar_serialize_and_parse_value_round_trip() -> None:
    definition = bramble.scalar(
        name="Base64",
        serialize=lambda v: base64.b64encode(v).decode("utf-8"),
        parse_value=lambda v: base64.b64decode(v),
    )

    wire_value = definition.serialize(b"hello")
    assert wire_value == "aGVsbG8="
    assert definition.parse_value(wire_value) == b"hello"


def test_scalar_parse_value_raises_on_invalid_input() -> None:
    def parse_value(value: str) -> bytes:
        return base64.b64decode(value, validate=True)

    definition = bramble.scalar(name="Base64", parse_value=parse_value)

    import binascii

    try:
        definition.parse_value("not valid base64!!")
    except binascii.Error:
        pass
    else:
        raise AssertionError("expected parse_value to raise for invalid input")


def test_schema_config_stores_scalar_map() -> None:
    definition = bramble.scalar(name="Base64")
    config = SchemaConfig(scalar_map={Base64: definition})

    assert config.scalar_map[Base64] is definition


def test_schema_config_defaults_to_empty_scalar_map() -> None:
    assert SchemaConfig().scalar_map == {}


def test_newtype_scalar_usable_directly_as_field_annotation() -> None:
    @bramble.type
    class Query:
        data: Base64

    instance = Query(data=b"hello")
    assert instance.data == b"hello"


def test_schema_stores_query_and_config() -> None:
    @bramble.type
    class Query:
        data: Base64

    definition = bramble.scalar(name="Base64")
    config = SchemaConfig(scalar_map={Base64: definition})

    schema = bramble.Schema(query=Query, config=config)

    assert schema.query is Query
    assert schema.config is config
    assert schema.config.scalar_map[Base64] is definition


def test_schema_defaults_to_fresh_schema_config() -> None:
    @bramble.type
    class Query:
        data: Base64

    schema = bramble.Schema(query=Query)

    assert isinstance(schema.config, SchemaConfig)
    assert schema.config.scalar_map == {}
