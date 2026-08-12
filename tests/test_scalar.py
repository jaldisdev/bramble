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
import json
import uuid
from typing import NewType

import pytest

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


# --- String coercion ------------------------------------------------------------------------------


class _OrmEnumValue:
    """Stands in for an ORM's own enum value (gel's `DerivedEnumValue`, say): not a `str`, but with
    a meaningful `__str__`.
    """

    def __str__(self) -> str:
        return "EU"


@bramble.type
class _StringCoercionQuery:
    @bramble.field
    def custom() -> str:
        return _OrmEnumValue()  # type: ignore[return-value]

    @bramble.field
    def number() -> str:
        return 42  # type: ignore[return-value]

    @bramble.field
    def flag() -> str:
        return True  # type: ignore[return-value]

    @bramble.field
    def already_a_string() -> str:
        return "plain"

    @bramble.field
    def container() -> str:
        return {"a": 1}  # type: ignore[return-value]


def test_a_custom_type_is_coerced_through_its_own_str() -> None:
    """A `String!` field has to serialize to a string. Passing an arbitrary object through puts
    something un-encodable in the response, which surfaces as a `TypeError` from the JSON encoder
    far from the cause -- exactly what an ORM enum value reaching a `str`-annotated field does.
    """
    result = bramble.Schema(query=_StringCoercionQuery).execute("{ custom }")

    assert result["data"] == {"custom": "EU"}
    json.dumps(result)  # the point of the coercion: the response is encodable


@pytest.mark.parametrize(
    ("field", "expected"),
    [("number", "42"), ("flag", "true"), ("alreadyAString", "plain")],
)
def test_builtin_scalars_coerce_predictably(field: str, expected: str) -> None:
    result = bramble.Schema(query=_StringCoercionQuery).execute(f"{{ {field} }}")
    assert result["data"] == {field: expected}


def test_a_builtin_container_is_rejected_rather_than_stringified() -> None:
    """Mirrors graphql-core's own split: a custom type is trusted to `__str__` meaningfully, but
    stringifying a `dict` into `"{'a': 1}"` would hide a genuine resolver bug behind output that
    looks plausible.
    """
    with pytest.raises(Exception, match="String cannot represent value"):
        bramble.Schema(query=_StringCoercionQuery).execute("{ container }")


# --- built-in scalars are declared when referenced ------------------------------------------------
#
# bramble names and serialises the standard-library date/decimal/UUID types with no registration at
# all, so a schema using them used to emit SDL that *referenced* `DateTime` while *defining* it
# nowhere -- output graphql-core rejects outright, and which disagreed with introspection.


@bramble.type
class _BuiltinScalarQuery:
    @bramble.field
    def when() -> datetime.datetime: ...

    @bramble.field
    def day() -> datetime.date: ...

    @bramble.field
    def clock() -> datetime.time: ...

    @bramble.field
    def amount() -> decimal.Decimal: ...

    @bramble.field
    def key() -> uuid.UUID: ...


def test_every_referenced_builtin_scalar_is_declared() -> None:
    sdl = str(bramble.Schema(query=_BuiltinScalarQuery))

    for name in ("Date", "DateTime", "Decimal", "Time", "UUID"):
        assert f"scalar {name}" in sdl, f"{name} is referenced by a field but never declared"


def test_the_declarations_carry_a_description() -> None:
    sdl = str(bramble.Schema(query=_BuiltinScalarQuery))

    assert '"""Date with time (isoformat)"""\nscalar DateTime' in sdl


@bramble.type
class _OnlyDateQuery:
    @bramble.field
    def day() -> datetime.date: ...


def test_an_unreferenced_builtin_is_not_declared() -> None:
    """Declaring all five unconditionally would put unused `scalar` lines into every schema."""
    sdl = str(bramble.Schema(query=_OnlyDateQuery))

    assert "scalar Date" in sdl
    for name in ("DateTime", "Decimal", "Time", "UUID"):
        assert f"scalar {name}" not in sdl


@bramble.type
class _NoBuiltinQuery:
    @bramble.field
    def name() -> str: ...


def test_a_schema_using_none_of_them_declares_none() -> None:
    sdl = str(bramble.Schema(query=_NoBuiltinQuery))

    assert "scalar " not in sdl


def test_the_spec_scalars_are_never_declared() -> None:
    """`Int`/`Float`/`String`/`Boolean`/`ID` are defined by the specification; declaring one is
    invalid.
    """

    @bramble.type
    class Query:
        @bramble.field
        def count() -> int: ...

        @bramble.field
        def label() -> str: ...

    sdl = str(bramble.Schema(query=Query))

    for name in ("Int", "Float", "String", "Boolean", "ID"):
        assert f"scalar {name}" not in sdl


def test_registering_one_explicitly_still_wins() -> None:
    """An explicit registration keeps its own description rather than being overwritten by the
    built-in default.
    """
    definition = bramble.scalar(
        name="DateTime",
        description="Our own wording",
        serialize=lambda value: value.isoformat(),
        parse_value=lambda value: value,
    )

    @bramble.type
    class Query:
        @bramble.field
        def when() -> datetime.datetime: ...

    schema = bramble.Schema(
        query=Query, config=SchemaConfig(scalar_map={datetime.datetime: definition})
    )
    sdl = str(schema)

    assert '"""Our own wording"""\nscalar DateTime' in sdl
    assert "Date with time (isoformat)" not in sdl
