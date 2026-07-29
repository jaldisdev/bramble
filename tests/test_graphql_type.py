from __future__ import annotations

import datetime
import decimal
import uuid
from typing import Annotated, NewType

import bramble


def test_builtin_scalar_types() -> None:
    @bramble.type
    class Query:
        name: str
        age: int
        ratio: float
        active: bool

    info = Query.__bramble_type_info__
    types_by_name = {f.name: f.graphql_type for f in info.fields}

    assert types_by_name == {
        "name": "String!",
        "age": "Int!",
        "ratio": "Float!",
        "active": "Boolean!",
    }


def test_id_type() -> None:
    @bramble.type
    class Query:
        identifier: bramble.ID

    assert Query.__bramble_type_info__.fields[0].graphql_type == "ID!"


def test_nullable_type() -> None:
    @bramble.type
    class Query:
        name: str | None

    field = Query.__bramble_type_info__.fields[0]
    assert field.graphql_type == "String"
    assert field.is_nullable is True


def test_list_types() -> None:
    @bramble.type
    class Query:
        tags: list[str]
        optional_tags: list[str] | None
        nullable_items: list[str | None]

    types_by_name = {f.name: f.graphql_type for f in Query.__bramble_type_info__.fields}

    assert types_by_name == {
        "tags": "[String!]!",
        "optional_tags": "[String!]",
        "nullable_items": "[String]!",
    }


def test_stdlib_scalar_types() -> None:
    @bramble.type
    class Query:
        created: datetime.datetime
        day: datetime.date
        amount: decimal.Decimal
        uid: uuid.UUID

    types_by_name = {f.name: f.graphql_type for f in Query.__bramble_type_info__.fields}

    assert types_by_name == {
        "created": "DateTime!",
        "day": "Date!",
        "amount": "Decimal!",
        "uid": "UUID!",
    }


def test_bramble_type_reference() -> None:
    @bramble.type
    class Author:
        name: str

    @bramble.type
    class Post:
        author: Author

    assert Post.__bramble_type_info__.fields[0].graphql_type == "Author!"


def test_custom_scalar_newtype_falls_back_to_its_own_name() -> None:
    Base64 = NewType("Base64", bytes)

    @bramble.type
    class Query:
        data: Base64

    assert Query.__bramble_type_info__.fields[0].graphql_type == "Base64!"


def test_argument_type_resolution() -> None:
    @bramble.type
    class Query:
        @bramble.field
        def greet(name: str, shout: bool = False) -> str:
            return name

    arguments = {a.name: a for a in Query.__bramble_type_info__.fields[0].arguments}
    assert arguments["name"].graphql_type == "String!"
    assert arguments["shout"].graphql_type == "Boolean!"


def test_argument_graphql_type_override() -> None:
    @bramble.type
    class Query:
        @bramble.field
        def greet(name: Annotated[str, bramble.argument(graphql_type=int)]) -> str:
            return name

    argument = Query.__bramble_type_info__.fields[0].arguments[0]
    assert argument.graphql_type == "Int!"
