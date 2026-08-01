#
# This source file is part of the Bramble open source project.
#
# Copyright (c) 2026 Jaldis B.V.
#
# Licensed under the MIT OR Apache-2.0 license (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://opensource.org/licenses/MIT
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

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


# `bramble.Upload`/`UploadDefinition` (matches the equivalent scalar in another popular Python
# GraphQL library's exact signature -- see project memory on not naming it in code/comments):
# a fully opaque pass-through scalar. bramble has no HTTP transport layer of its own, so there's
# no multipart-request parsing to test here -- just that the scalar type-checks and round-trips
# whatever value a caller puts into `variable_values`, registered or not.


def test_upload_round_trips_unregistered() -> None:
    @bramble.type
    class Query:
        @bramble.field
        def echo(file: bramble.Upload) -> bramble.Upload:
            return file

    schema = bramble.Schema(query=Query)

    assert "echo(file: Upload!): Upload!" in schema.to_sdl()
    assert "scalar Upload" not in schema.to_sdl()

    result = schema.execute("query($f: Upload!) { echo(file: $f) }", variable_values={"f": b"hello"})
    assert result == {"data": {"echo": b"hello"}}


def test_upload_round_trips_an_arbitrary_object_not_just_bytes() -> None:
    class FakeUploadFile:
        def __init__(self, name: str) -> None:
            self.name = name

    @bramble.type
    class Query:
        @bramble.field
        def echo(file: bramble.Upload) -> bramble.Upload:
            return file

    schema = bramble.Schema(query=Query)
    upload = FakeUploadFile("photo.png")

    result = schema.execute("query($f: Upload!) { echo(file: $f) }", variable_values={"f": upload})
    assert result["data"]["echo"] is upload


def test_upload_definition_matches_the_expected_fields() -> None:
    assert bramble.UploadDefinition.name == "Upload"
    assert bramble.UploadDefinition.description == "Represents a file upload."
    assert bramble.UploadDefinition.serialize(b"x") == b"x"
    assert bramble.UploadDefinition.parse_value(b"x") == b"x"


def test_upload_registered_via_scalar_map_declares_scalar_in_sdl() -> None:
    @bramble.type
    class Query:
        @bramble.field
        def echo(file: bramble.Upload) -> bramble.Upload:
            return file

    schema = bramble.Schema(
        query=Query, config=SchemaConfig(scalar_map={bramble.Upload: bramble.UploadDefinition})
    )
    sdl = schema.to_sdl()

    assert '"""Represents a file upload."""\nscalar Upload' in sdl
    result = schema.execute("query($f: Upload!) { echo(file: $f) }", variable_values={"f": b"hello"})
    assert result == {"data": {"echo": b"hello"}}


def test_upload_as_mutation_argument() -> None:
    @bramble.type
    class Query:
        ok: bool = True

    @bramble.type
    class Mutation:
        @bramble.field
        def upload_avatar(file: bramble.Upload) -> str:
            return f"received {len(file)} bytes"

    schema = bramble.Schema(query=Query, mutation=Mutation)

    result = schema.execute(
        "mutation($f: Upload!) { uploadAvatar(file: $f) }", variable_values={"f": b"12345"}
    )
    assert result == {"data": {"uploadAvatar": "received 5 bytes"}}
