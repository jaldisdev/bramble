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

import enum

import pytest

import bramble
from bramble.schema_directive import Location

# Enum classes referenced from another class's annotations have to live at module level, same
# convention as the rest of the suite (`typing.get_type_hints` can't see a test function's locals).


@bramble.enum
class Color(enum.Enum):
    RED = "red"
    GREEN = bramble.enum_value("green", description="The colour green")
    LEGACY_BLUE = bramble.enum_value("blue", name="BLUE", deprecation_reason="use GREEN")


@bramble.input
class ColorFilter:
    color: Color
    fallbacks: list[Color] | None = None


@bramble.enum
class Priority(enum.Enum):
    LOW = 1
    HIGH = 2


@bramble.type
class PriorityQuery:
    @bramble.field
    def priority() -> Priority:
        return 2  # an int-valued member's value, not the member


@bramble.input
class Numbers:
    count: int


@bramble.type
class NumberQuery:
    @bramble.field
    def go(numbers: Numbers) -> str:
        return "ok"


@bramble.type
class Query:
    @bramble.field
    def favourite() -> Color:
        return Color.RED

    @bramble.field
    def renamed() -> Color:
        return Color.LEGACY_BLUE

    @bramble.field
    def pick(color: Color) -> str:
        return f"{color.name}={color.value}"

    @bramble.field
    def many(colors: list[Color]) -> str:
        return ",".join(color.name for color in colors)

    @bramble.field
    def nested(filter: ColorFilter) -> str:
        fallbacks = ",".join(c.name for c in (filter.fallbacks or []))
        return f"{filter.color.name}|{fallbacks}"

    @bramble.field
    def maybe(color: Color | None = None) -> str:
        return "none" if color is None else color.name

    # The shape a database row or ORM field produces: the member's underlying value, not the
    # member. Declared here rather than in each test so they share one schema.
    @bramble.field
    def from_value() -> Color:
        return "green"

    @bramble.field
    def renamed_from_value() -> Color:
        return "blue"

    @bramble.field
    def unknown_value() -> Color | None:
        return "chartreuse"

    @bramble.field
    def required_unknown_value() -> Color:
        return "chartreuse"

    @bramble.field
    def values_in_a_list() -> list[Color | None]:
        return ["red", "chartreuse"]


def _schema() -> bramble.Schema:
    return bramble.Schema(query=Query, types=[ColorFilter])


# --- Declaration / SDL -------------------------------------------------------------------------


def test_enum_renders_as_a_real_graphql_enum() -> None:
    assert "enum Color {" in _schema().to_sdl()


def test_member_graphql_name_is_the_python_identifier_not_the_value() -> None:
    sdl = _schema().to_sdl()
    assert "\n  RED\n" in sdl
    assert '"red"' not in sdl


def test_enum_value_overrides_name_description_and_deprecation() -> None:
    sdl = _schema().to_sdl()
    assert '"""The colour green"""' in sdl
    assert 'BLUE @deprecated(reason: "use GREEN")' in sdl
    assert "LEGACY_BLUE" not in sdl


def test_enum_decorator_preserves_python_enum_semantics() -> None:
    """`enum_value(...)` stands in for a member's value during class construction -- the real value
    has to be restored afterwards, or ordinary Python use of the enum breaks.
    """
    assert Color.GREEN.value == "green"
    assert Color.LEGACY_BLUE.value == "blue"
    assert Color("green") is Color.GREEN
    assert Color["LEGACY_BLUE"] is Color.LEGACY_BLUE


def test_enum_type_is_discovered_without_being_listed_in_types() -> None:
    assert "Color" in bramble.Schema(query=Query, types=[ColorFilter]).types_by_name


def test_decorating_a_non_enum_raises_schema_error() -> None:
    with pytest.raises(bramble.SchemaError, match="not an enum"):

        @bramble.enum
        class NotAnEnum:
            pass


def test_enum_name_and_description_can_be_overridden() -> None:
    @bramble.enum(name="Shade", description="A shade of grey")
    class Grey(enum.Enum):
        LIGHT = "light"

    info = Grey.__bramble_type_info__
    assert info.name == "Shade"
    assert info.description == "A shade of grey"


# --- Output serialization -----------------------------------------------------------------------


def test_resolved_member_serializes_to_its_graphql_name() -> None:
    assert _schema().execute("{ favourite }") == {"data": {"favourite": "RED"}}


def test_resolved_member_uses_its_name_override() -> None:
    assert _schema().execute("{ renamed }") == {"data": {"renamed": "BLUE"}}


def test_a_resolver_may_return_the_members_underlying_value() -> None:
    """Gel rows, ORM fields, and plain dicts hand back the value rather than the member -- and this
    used to raise `AttributeError: 'str' object has no attribute 'name'` out of serialization.
    """
    assert _schema().execute("{ fromValue }") == {"data": {"fromValue": "GREEN"}}


def test_an_underlying_value_resolves_through_a_members_name_override() -> None:
    assert _schema().execute("{ renamedFromValue }") == {"data": {"renamedFromValue": "BLUE"}}


def test_a_non_string_underlying_value_is_looked_up_through_the_enum_class() -> None:
    assert bramble.Schema(query=PriorityQuery).execute("{ priority }") == {"data": {"priority": "HIGH"}}


def test_a_value_that_is_no_member_at_all_becomes_a_field_error() -> None:
    result = _schema().execute("{ unknownValue }")

    assert result["data"] == {"unknownValue": None}
    (error,) = result["errors"]
    assert error["message"] == "'chartreuse' is not a member of enum 'Color'"
    assert error["path"] == ["unknownValue"]


def test_an_unserializable_enum_leaf_bubbles_like_any_other_null() -> None:
    """The failure nulls the leaf and bubbles from there -- one bad row shouldn't take the whole
    response down, and inside a list only that item is lost.
    """
    assert _schema().execute("{ requiredUnknownValue }")["data"] is None

    result = _schema().execute("{ valuesInAList }")
    assert result["data"] == {"valuesInAList": ["RED", None]}
    assert result["errors"][0]["path"] == ["valuesInAList", 1]


# --- Input coercion -----------------------------------------------------------------------------


def test_argument_is_coerced_to_the_python_member() -> None:
    assert _schema().execute("{ pick(color: GREEN) }") == {"data": {"pick": "GREEN=green"}}


def test_argument_accepts_a_renamed_member_by_its_graphql_name() -> None:
    assert _schema().execute("{ pick(color: BLUE) }") == {"data": {"pick": "LEGACY_BLUE=blue"}}


def test_list_of_enums_is_coerced_elementwise() -> None:
    assert _schema().execute("{ many(colors: [RED, GREEN]) }") == {"data": {"many": "RED,GREEN"}}


def test_enum_nested_in_an_input_object_is_coerced() -> None:
    result = _schema().execute("{ nested(filter: {color: RED, fallbacks: [GREEN]}) }")
    assert result == {"data": {"nested": "RED|GREEN"}}


def test_omitted_nullable_enum_argument_stays_none() -> None:
    assert _schema().execute("{ maybe }") == {"data": {"maybe": "none"}}


def test_enum_supplied_through_a_variable() -> None:
    result = _schema().execute("query($c: Color!) { pick(color: $c) }", variable_values={"c": "GREEN"})
    assert result == {"data": {"pick": "GREEN=green"}}


# --- Validation ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "message"),
    [
        ("{ pick(color: NOPE) }", "not a valid value for enum 'Color'"),
        # The Python identifier of a member that declared a `name=` override is not its GraphQL
        # name, so querying by it must be rejected the same as any other unknown value.
        ("{ pick(color: LEGACY_BLUE) }", "not a valid value for enum 'Color'"),
        # A quoted `"RED"` is a String literal, which the spec keeps distinct from an enum value.
        ('{ pick(color: "RED") }', "expected a value of enum 'Color'"),
        ("{ pick(color: 3) }", "expected a value of enum 'Color'"),
        ("{ nested(filter: {color: NOPE}) }", "not a valid value for enum 'Color'"),
    ],
)
def test_invalid_enum_values_are_rejected_at_validation(query: str, message: str) -> None:
    with pytest.raises(bramble.GraphQLError, match=message):
        _schema().validate_query(query)


def test_input_object_fields_are_type_checked() -> None:
    """Recursing into an input object's own fields was added alongside enums (it's what catches an
    invalid enum one level down) -- it fixes plain scalars in that position at the same time.
    """
    schema = bramble.Schema(query=NumberQuery, types=[Numbers])
    with pytest.raises(bramble.GraphQLError, match="expected an integer"):
        schema.validate_query('{ go(numbers: {count: "not an int"}) }')


# --- Directives ---------------------------------------------------------------------------------


@bramble.schema_directive(locations=[Location.ENUM])
class EnumTag:
    note: str


@bramble.schema_directive(locations=[Location.ENUM_VALUE])
class ValueTag:
    note: str


@bramble.enum(directives=[EnumTag(note="on the enum")])
class Status(enum.Enum):
    OPEN = bramble.enum_value("open", directives=[ValueTag(note="on the member")])


@bramble.type
class StatusQuery:
    @bramble.field
    def status() -> Status:
        return Status.OPEN


def test_directives_render_at_enum_and_enum_value_locations() -> None:
    sdl = bramble.Schema(query=StatusQuery).to_sdl()
    assert 'enum Status @enumTag(note: "on the enum")' in sdl
    assert 'OPEN @valueTag(note: "on the member")' in sdl
    assert "directive @enumTag(note: String!) on ENUM" in sdl
    assert "directive @valueTag(note: String!) on ENUM_VALUE" in sdl


def test_directive_at_the_wrong_location_is_rejected() -> None:
    with pytest.raises(bramble.SchemaError, match="cannot be applied"):

        @bramble.enum(directives=[ValueTag(note="ENUM_VALUE is not ENUM")])
        class Bad(enum.Enum):
            X = "x"


# --- Codegen ------------------------------------------------------------------------------------


def test_codegen_treats_an_enum_as_a_leaf_not_an_empty_object() -> None:
    """An enum has members, not fields, so the codegen walker must not descend into it as an
    object -- doing so used to emit an empty `class ...Favourite: pass` and reference it as the
    field's type, which is both wrong and (for a variable) uncompilable.
    """
    from bramble.codegen import PythonPlugin, generate_operation

    operation = generate_operation(
        _schema(), "query C($c: Color!) { favourite pick(color: $c) }"
    )
    code = PythonPlugin().generate_code(operation)

    namespace: dict[str, object] = {}
    exec(compile(code, "<generated>", "exec"), namespace)  # noqa: S102 -- verifying our own output compiles.

    result = namespace["CResult"](favourite="RED", pick="x")
    assert result.favourite == "RED"
    assert namespace["CVariables"](c="GREEN").c == "GREEN"
    assert "class CResultFavourite" not in code
