from __future__ import annotations

import asyncio
import base64
import datetime
import decimal
import uuid
from typing import Annotated, NewType, Union

import pytest

import bramble
from bramble.directive import DirectiveLocation, DirectiveValue
from bramble.schema.config import SchemaConfig

# `typing.get_type_hints` (used both for a type's own fields and a resolver's own parameters) can
# only see module globals, never an enclosing test function's locals -- so any class referenced
# *from another class's annotation* (a field type, a resolver's return type) has to live at module
# level here, matching test_schema.py's own established convention. A class only ever referencing
# itself (`Parent[Circle]` inside `Circle`) doesn't have this problem and can stay local.


@bramble.type
class _Author:
    name: str
    email: str = "ada@example.com"


@bramble.type
class _Post:
    title: str
    author: _Author


@bramble.type
class _Item:
    label: str


@bramble.type
class _FailingInner:
    @bramble.field
    def required() -> str:
        raise ValueError("boom")


@bramble.type
class _ListItem:
    @bramble.field
    def value(should_fail: bool) -> str:
        if should_fail:
            raise ValueError("bad item")
        return "ok"


@bramble.interface
class _Shape:
    @bramble.field
    def area() -> float:
        raise NotImplementedError


@bramble.type
class _Circle(_Shape):
    radius: float

    @bramble.field
    def area(parent: bramble.Parent["_Circle"]) -> float:
        return 3.14 * parent.radius**2

    @classmethod
    def is_type_of(cls, obj: object, info: object) -> bool:
        return isinstance(obj, _Circle)


@bramble.type
class _Square(_Shape):
    side: float

    @bramble.field
    def area(parent: bramble.Parent["_Square"]) -> float:
        return parent.side**2

    @classmethod
    def is_type_of(cls, obj: object, info: object) -> bool:
        return isinstance(obj, _Square)


class _AudioRecord:
    def __init__(self, title: str) -> None:
        self.title = title


@bramble.type
class _Audio:
    title: str


@bramble.type
class _Video:
    title: str


def _resolve_media_type(obj: object, info: object) -> type:
    return _Audio if isinstance(obj, _AudioRecord) else _Video


MediaItem = Annotated[Union[_Audio, _Video], bramble.union("MediaItem", resolve_type=_resolve_media_type)]
BareMediaItem = Union[_Audio, _Video]

Base64 = NewType("Base64", bytes)


@bramble.type
class _InfoInner:
    label: str


def test_scalar_leaf_field() -> None:
    @bramble.type
    class Query:
        @bramble.field
        def greet(name: str) -> str:
            return f"hello {name}"

    schema = bramble.Schema(query=Query)
    result = schema.execute('query { greet(name: "Ada") }')

    assert result == {"data": {"greet": "hello Ada"}}


def test_nested_object_field() -> None:
    @bramble.type
    class Query:
        @bramble.field
        def post() -> _Post:
            return _Post(title="Hello", author=_Author(name="Ada"))

    schema = bramble.Schema(query=Query)
    result = schema.execute("query { post { title author { name } } }")

    assert result == {"data": {"post": {"title": "Hello", "author": {"name": "Ada"}}}}


def test_overlapping_fragments_merge_sub_selections_for_the_same_response_key() -> None:
    """§8's `CollectFields`: `post` appears twice -- once directly, once via a fragment -- each
    requesting a *different* sub-field of `author`. Both must appear in the result; the second
    occurrence must not silently discard the first's sub-selection.
    """

    @bramble.type
    class Query:
        @bramble.field
        def post() -> _Post:
            return _Post(title="Hello", author=_Author(name="Ada", email="ada@example.com"))

    schema = bramble.Schema(query=Query)
    result = schema.execute(
        """
        query {
            post {
                title
                author { name }
            }
            ...F
        }
        fragment F on Query {
            post {
                author { email }
            }
        }
        """
    )

    assert result == {
        "data": {"post": {"title": "Hello", "author": {"name": "Ada", "email": "ada@example.com"}}}
    }


def test_list_field() -> None:
    @bramble.type
    class Query:
        @bramble.field
        def items() -> list[_Item]:
            return [_Item(label="a"), _Item(label="b")]

    schema = bramble.Schema(query=Query)
    result = schema.execute("query { items { label } }")

    assert result == {"data": {"items": [{"label": "a"}, {"label": "b"}]}}


def test_alias_is_used_as_response_key() -> None:
    @bramble.type
    class Query:
        @bramble.field
        def greet(name: str) -> str:
            return f"hi {name}"

    schema = bramble.Schema(query=Query)
    result = schema.execute('query { hi: greet(name: "Bo") }')

    assert result == {"data": {"hi": "hi Bo"}}


def test_field_name_override_is_the_only_valid_query_name() -> None:
    @bramble.type
    class Query:
        internal_name: str = bramble.field(name="publicName", default="hi")

    schema = bramble.Schema(query=Query)
    root = Query()

    assert schema.execute("query { publicName }", root_value=root) == {"data": {"publicName": "hi"}}

    with pytest.raises(bramble.GraphQLError, match="internalName"):
        schema.execute("query { internalName }", root_value=root)


def test_variables_resolve_into_arguments() -> None:
    @bramble.type
    class Query:
        @bramble.field
        def greet(name: str) -> str:
            return f"hi {name}"

    schema = bramble.Schema(query=Query)
    result = schema.execute("query($n: String!) { greet(name: $n) }", variable_values={"n": "Cy"})

    assert result == {"data": {"greet": "hi Cy"}}


def test_plain_dataclass_field_reads_from_root_value() -> None:
    @bramble.type
    class Query:
        greeting: str

    schema = bramble.Schema(query=Query)
    result = schema.execute("query { greeting }", root_value=Query(greeting="hi from root"))

    assert result == {"data": {"greeting": "hi from root"}}


def test_async_resolver_is_awaited() -> None:
    @bramble.type
    class Query:
        @bramble.field
        async def greet(name: str) -> str:
            return f"async hi {name}"

    schema = bramble.Schema(query=Query)
    result = schema.execute('query { greet(name: "Ada") }')

    assert result == {"data": {"greet": "async hi Ada"}}


def test_typename_meta_field() -> None:
    @bramble.type
    class Query:
        @bramble.field
        def hello() -> str:
            return "hi"

    schema = bramble.Schema(query=Query)
    result = schema.execute("query { __typename }")

    assert result == {"data": {"__typename": "Query"}}


# --- Null bubbling -----------------------------------------------------------------------------


def test_nullable_field_error_nulls_only_that_key() -> None:
    @bramble.type
    class Query:
        @bramble.field
        def maybe() -> str | None:
            raise ValueError("boom")

        @bramble.field
        def other() -> str:
            return "fine"

    schema = bramble.Schema(query=Query)
    result = schema.execute("query { maybe other }")

    assert result["data"] == {"maybe": None, "other": "fine"}
    assert len(result["errors"]) == 1
    assert result["errors"][0]["message"] == "boom"
    assert result["errors"][0]["path"] == ["maybe"]


def test_non_null_field_error_propagates_to_null_the_whole_response() -> None:
    @bramble.type
    class Query:
        @bramble.field
        def required() -> str:
            raise ValueError("boom")

    schema = bramble.Schema(query=Query)
    result = schema.execute("query { required }")

    assert result["data"] is None
    assert result["errors"][0]["path"] == ["required"]


def test_execution_error_reports_the_failing_field_source_location() -> None:
    @bramble.type
    class Query:
        @bramble.field
        def required() -> str:
            raise ValueError("boom")

    schema = bramble.Schema(query=Query)
    result = schema.execute("query {\n  required\n}")

    assert result["errors"][0]["locations"] == [{"line": 2, "column": 3}]


def test_non_null_child_field_nulls_nullable_parent_object() -> None:
    @bramble.type
    class Query:
        @bramble.field
        def inner() -> _FailingInner | None:
            return _FailingInner()

        @bramble.field
        def sibling() -> str:
            return "fine"

    schema = bramble.Schema(query=Query)
    result = schema.execute("query { inner { required } sibling }")

    assert result["data"] == {"inner": None, "sibling": "fine"}
    assert result["errors"][0]["path"] == ["inner", "required"]


def test_non_null_child_field_propagates_past_non_null_parent() -> None:
    @bramble.type
    class Query:
        @bramble.field
        def inner() -> _FailingInner:
            return _FailingInner()

    schema = bramble.Schema(query=Query)
    result = schema.execute("query { inner { required } }")

    assert result["data"] is None
    assert result["errors"][0]["path"] == ["inner", "required"]


def test_cannot_return_null_for_non_null_field_is_a_field_error() -> None:
    @bramble.type
    class Query:
        @bramble.field
        def required() -> str:
            return None  # type: ignore[return-value]

    schema = bramble.Schema(query=Query)
    result = schema.execute("query { required }")

    assert result["data"] is None
    assert "non-nullable" in result["errors"][0]["message"].lower()


def test_nullable_list_item_failure_nulls_just_that_item() -> None:
    @bramble.type
    class Query:
        @bramble.field
        def items() -> list[_ListItem | None]:
            return [_ListItem(), _ListItem()]

    schema = bramble.Schema(query=Query)
    result = schema.execute("query { items { value(shouldFail: true) } }")

    assert result["data"] == {"items": [None, None]}
    assert len(result["errors"]) == 2


def test_non_null_list_item_failure_nulls_the_whole_list() -> None:
    @bramble.type
    class Query:
        @bramble.field
        def items() -> list[_ListItem] | None:
            return [_ListItem(), _ListItem()]

        @bramble.field
        def sibling() -> str:
            return "fine"

    schema = bramble.Schema(query=Query)
    result = schema.execute("query { items { value(shouldFail: true) } sibling }")

    assert result["data"] == {"items": None, "sibling": "fine"}
    # Execution aborts the list on the first failing item rather than continuing.
    assert len(result["errors"]) == 1


def test_non_null_list_item_failure_propagates_past_a_non_null_list_field() -> None:
    @bramble.type
    class Query:
        @bramble.field
        def items() -> list[_ListItem]:
            return [_ListItem(), _ListItem()]

    schema = bramble.Schema(query=Query)
    result = schema.execute("query { items { value(shouldFail: true) } }")

    assert result["data"] is None
    assert len(result["errors"]) == 1


def test_resolver_raised_graphql_error_keeps_its_own_code_and_extensions() -> None:
    @bramble.type
    class Query:
        @bramble.field
        def item() -> str | None:
            raise bramble.GraphQLError(
                "not found", code=bramble.ErrorCode.UNKNOWN_FIELD, extensions={"itemId": "42"}
            )

    schema = bramble.Schema(query=Query)
    result = schema.execute("query { item }")

    assert result["data"] == {"item": None}
    error = result["errors"][0]
    assert error["message"] == "not found"
    assert error["extensions"] == {"code": "UNKNOWN_FIELD", "itemId": "42"}
    assert error["path"] == ["item"]


# --- skip/include integration -------------------------------------------------------------------


def test_skip_directive_removes_field_from_response() -> None:
    @bramble.type
    class Query:
        @bramble.field
        def greet(name: str) -> str:
            return f"hi {name}"

    schema = bramble.Schema(query=Query)
    result = schema.execute(
        'query($s: Boolean!) { greet(name: "x") @skip(if: $s) }', variable_values={"s": True}
    )

    assert result == {"data": {}}


# --- Interfaces ----------------------------------------------------------------------------------


def test_interface_dispatch_via_is_type_of() -> None:
    @bramble.type
    class Query:
        @bramble.field
        def shape() -> _Shape:
            return _Circle(radius=2.0)

    schema = bramble.Schema(query=Query, types=[_Circle, _Square])
    result = schema.execute(
        """
        query {
            shape {
                __typename
                area
                ... on _Circle { radius }
                ... on _Square { side }
            }
        }
        """
    )

    assert result == {
        "data": {
            "shape": {"__typename": "_Circle", "area": 12.56, "radius": 2.0},
        }
    }


def test_interface_type_condition_excludes_non_matching_fragment() -> None:
    @bramble.type
    class Query:
        @bramble.field
        def shape() -> _Shape:
            return _Square(side=3.0)

    schema = bramble.Schema(query=Query, types=[_Circle, _Square])
    result = schema.execute(
        """
        query {
            shape {
                __typename
                ... on _Circle { radius }
                ... on _Square { side }
            }
        }
        """
    )

    assert result == {"data": {"shape": {"__typename": "_Square", "side": 3.0}}}


def test_unresolvable_interface_type_raises_graphql_error() -> None:
    @bramble.type
    class Query:
        @bramble.field
        def shape() -> _Shape:
            return object()  # type: ignore[return-value]

    schema = bramble.Schema(query=Query, types=[_Circle, _Square])
    with pytest.raises(bramble.GraphQLError) as excinfo:
        schema.execute("query { shape { __typename } }")

    assert excinfo.value.code is bramble.ErrorCode.INTERFACE_TYPE_RESOLUTION_FAILED


# --- Unions --------------------------------------------------------------------------------------


def test_union_dispatch_via_resolve_type() -> None:
    """`_resolve_media_type` dispatches on the *domain* object's own type (`_AudioRecord`), not
    the GraphQL type -- matching how a real resolver would return a raw domain record rather than
    an instance of the `@bramble.type`-decorated class itself.
    """

    @bramble.type
    class Query:
        @bramble.field
        def media() -> MediaItem:
            return _AudioRecord(title="song")

    schema = bramble.Schema(query=Query, types=[_Audio, _Video])
    result = schema.execute(
        """
        query {
            media {
                __typename
                ... on _Audio { title }
                ... on _Video { title }
            }
        }
        """
    )

    assert result == {"data": {"media": {"__typename": "_Audio", "title": "song"}}}


def test_bare_union_dispatch_via_isinstance_fallback() -> None:
    @bramble.type
    class Query:
        @bramble.field
        def media() -> BareMediaItem:
            return _Video(title="clip")

    schema = bramble.Schema(query=Query, types=[_Audio, _Video])
    result = schema.execute("query { media { __typename ... on _Video { title } } }")

    assert result == {"data": {"media": {"__typename": "_Video", "title": "clip"}}}


# --- Custom operation directives ------------------------------------------------------------------


def test_custom_directive_transforms_resolved_value() -> None:
    @bramble.directive(locations=[DirectiveLocation.FIELD])
    def turn_uppercase(value: DirectiveValue[str]) -> str:
        return value.upper()

    @bramble.type
    class Query:
        @bramble.field
        def greet(name: str) -> str:
            return f"hi {name}"

    schema = bramble.Schema(query=Query, directives=[turn_uppercase])
    result = schema.execute('query { greet(name: "ada") @turnUppercase }')

    assert result == {"data": {"greet": "HI ADA"}}


def test_custom_directive_with_arguments() -> None:
    @bramble.directive(locations=[DirectiveLocation.FIELD])
    def replace(value: DirectiveValue[str], old: str, new: str) -> str:
        return value.replace(old, new)

    @bramble.type
    class Query:
        @bramble.field
        def name() -> str:
            return "JohnDoe"

    schema = bramble.Schema(query=Query, directives=[replace])
    result = schema.execute('query { name @replace(old: "John", new: "Jane") }')

    assert result == {"data": {"name": "JaneDoe"}}


# --- Mutations -------------------------------------------------------------------------------------


def test_mutation_fields_execute_serially_in_query_order() -> None:
    calls: list[str] = []

    @bramble.type
    class Query:
        @bramble.field
        def noop() -> str:
            return "noop"

    @bramble.type
    class Mutation:
        @bramble.field
        def step_a() -> str:
            calls.append("a")
            return "a"

        @bramble.field
        def step_b() -> str:
            calls.append("b")
            return "b"

    schema = bramble.Schema(query=Query, mutation=Mutation)
    result = schema.execute("mutation { stepA stepB }")

    assert result == {"data": {"stepA": "a", "stepB": "b"}}
    assert calls == ["a", "b"]


def test_mutation_on_schema_without_mutation_type_raises() -> None:
    @bramble.type
    class Query:
        @bramble.field
        def noop() -> str:
            return "noop"

    schema = bramble.Schema(query=Query)
    with pytest.raises(bramble.GraphQLError):
        schema.execute("mutation { noop }")


# --- Scalars ---------------------------------------------------------------------------------------


def test_builtin_datetime_serializes_to_isoformat() -> None:
    @bramble.type
    class Query:
        @bramble.field
        def when() -> datetime.datetime:
            return datetime.datetime(2024, 1, 1, 12, 0, 0)

    schema = bramble.Schema(query=Query)
    result = schema.execute("query { when }")

    assert result == {"data": {"when": "2024-01-01T12:00:00"}}


def test_builtin_decimal_and_uuid_serialize_to_str() -> None:
    fixed_uuid = uuid.uuid4()

    @bramble.type
    class Query:
        @bramble.field
        def price() -> decimal.Decimal:
            return decimal.Decimal("9.99")

        @bramble.field
        def identifier() -> uuid.UUID:
            return fixed_uuid

    schema = bramble.Schema(query=Query)
    result = schema.execute("query { price identifier }")

    assert result == {"data": {"price": "9.99", "identifier": str(fixed_uuid)}}


def test_custom_scalar_serialize_and_parse_value_round_trip() -> None:
    @bramble.type
    class Query:
        @bramble.field
        def encoded(data: Base64) -> Base64:
            return data

    config = SchemaConfig(
        scalar_map={
            Base64: bramble.scalar(
                name="Base64",
                serialize=lambda value: base64.b64encode(value).decode("utf-8"),
                parse_value=lambda value: base64.b64decode(value),
            )
        }
    )
    schema = bramble.Schema(query=Query, config=config)
    result = schema.execute('query { encoded(data: "aGVsbG8=") }')

    assert result == {"data": {"encoded": "aGVsbG8="}}


# --- Info / selected_fields --------------------------------------------------------------------


def test_info_exposes_field_name_path_and_selected_fields() -> None:
    captured: dict[str, object] = {}

    @bramble.type
    class Query:
        @bramble.field
        def inner(info: bramble.Info) -> _InfoInner:
            captured["field_name"] = info.field_name
            captured["path"] = info.path.as_list()
            captured["selected_field_names"] = [f.name for f in info.selected_fields]
            return _InfoInner(label="x")

    schema = bramble.Schema(query=Query)
    result = schema.execute("query { inner { label } }")

    assert result == {"data": {"inner": {"label": "x"}}}
    assert captured["field_name"] == "inner"
    assert captured["path"] == ["inner"]
    assert captured["selected_field_names"] == ["label"]


def test_info_context_and_variable_values_are_threaded_through() -> None:
    @bramble.type
    class Query:
        @bramble.field
        def value(info: bramble.Info) -> str:
            return f"{info.context}-{info.variable_values.get('n')}"

    schema = bramble.Schema(query=Query)
    result = schema.execute("query($n: Int!) { value }", variable_values={"n": 5}, context="ctx")

    assert result == {"data": {"value": "ctx-5"}}


# --- Async entry point -------------------------------------------------------------------------


def test_execute_async_directly() -> None:
    @bramble.type
    class Query:
        @bramble.field
        def greet(name: str) -> str:
            return f"hi {name}"

    schema = bramble.Schema(query=Query)
    result = asyncio.run(schema.execute_async('query { greet(name: "Ada") }'))

    assert result == {"data": {"greet": "hi Ada"}}


# --- auto_camel_case -----------------------------------------------------------------------------


def test_snake_case_field_and_argument_are_queryable_as_camel_case_by_default() -> None:
    @bramble.type
    class Query:
        @bramble.field
        def post_by_slug(post_id: str) -> str:
            return f"post {post_id}"

    schema = bramble.Schema(query=Query)
    result = schema.execute('query { postBySlug(postId: "p1") }')

    assert result == {"data": {"postBySlug": "post p1"}}


def test_snake_case_names_are_no_longer_queryable_once_camel_cased() -> None:
    @bramble.type
    class Query:
        @bramble.field
        def post_by_slug(post_id: str) -> str:
            return f"post {post_id}"

    schema = bramble.Schema(query=Query)

    with pytest.raises(bramble.GraphQLError, match="post_by_slug"):
        schema.execute('query { post_by_slug(post_id: "p1") }')


def test_auto_camel_case_false_keeps_raw_snake_case_names() -> None:
    @bramble.type
    class Query:
        @bramble.field
        def post_by_slug(post_id: str) -> str:
            return f"post {post_id}"

    schema = bramble.Schema(query=Query, config=SchemaConfig(auto_camel_case=False))
    result = schema.execute('query { post_by_slug(post_id: "p1") }')

    assert result == {"data": {"post_by_slug": "post p1"}}


def test_explicit_name_override_takes_priority_over_auto_camel_case() -> None:
    @bramble.type
    class Query:
        @bramble.field(name="customName")
        def post_by_slug(post_id: str) -> str:
            return f"post {post_id}"

    schema = bramble.Schema(query=Query)
    result = schema.execute('query { customName(postId: "p1") }')

    assert result == {"data": {"customName": "post p1"}}
