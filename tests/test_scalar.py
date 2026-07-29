from __future__ import annotations

import base64
import datetime
import decimal
import uuid
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


# Built-in scalar coverage, output-direction (serialization) only. bramble has no built-in scalar
# *input* parsing for these types yet (a resolver argument would receive the raw string unparsed,
# not a real `datetime.date`/`Decimal`/`UUID` instance) -- see project memory for this flagged, not
# silently skipped, gap. `datetime.time` -> `Time` was added this session; it wasn't recognized as
# a scalar at all before (fell back to an invalid lowercase `time` type name and leaked the raw
# Python object into the response).


def test_builtin_date_serializes_to_isoformat() -> None:
    @bramble.type
    class Query:
        @bramble.field
        def today() -> datetime.date:
            return datetime.date(2024, 1, 1)

    schema = bramble.Schema(query=Query)

    assert "today: Date!" in schema.to_sdl()
    assert schema.execute("{ today }") == {"data": {"today": "2024-01-01"}}


def test_builtin_time_serializes_to_isoformat() -> None:
    @bramble.type
    class Query:
        @bramble.field
        def now() -> datetime.time:
            return datetime.time(9, 30, 0)

    schema = bramble.Schema(query=Query)

    assert "now: Time!" in schema.to_sdl()
    assert schema.execute("{ now }") == {"data": {"now": "09:30:00"}}


def test_builtin_datetime_serializes_to_isoformat() -> None:
    @bramble.type
    class Query:
        @bramble.field
        def created_at() -> datetime.datetime:
            return datetime.datetime(2024, 1, 1, 9, 30, 0)

    schema = bramble.Schema(query=Query)

    assert "createdAt: DateTime!" in schema.to_sdl()
    assert schema.execute("{ createdAt }") == {"data": {"createdAt": "2024-01-01T09:30:00"}}


def test_builtin_decimal_serializes_to_str_preserving_precision() -> None:
    @bramble.type
    class Query:
        @bramble.field
        def price() -> decimal.Decimal:
            return decimal.Decimal("9.99")

    schema = bramble.Schema(query=Query)

    assert "price: Decimal!" in schema.to_sdl()
    assert schema.execute("{ price }") == {"data": {"price": "9.99"}}


def test_builtin_uuid_serializes_to_str() -> None:
    fixed_uuid = uuid.uuid4()

    @bramble.type
    class Query:
        @bramble.field
        def identifier() -> uuid.UUID:
            return fixed_uuid

    schema = bramble.Schema(query=Query)

    assert "identifier: UUID!" in schema.to_sdl()
    assert schema.execute("{ identifier }") == {"data": {"identifier": str(fixed_uuid)}}
