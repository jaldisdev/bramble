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

import pytest
from bramble._bramble import lower_query

import bramble


def field_names(fields) -> list[str]:
    return [f.field_name for f in fields]


def test_lower_query_with_no_directives_keeps_everything() -> None:
    _, result = lower_query("query { a b c }", variable_values={})
    assert field_names(result) == ["a", "b", "c"]


def test_lower_query_reports_operation_type() -> None:
    operation_type, _ = lower_query("query { a }", variable_values={})
    assert operation_type == "query"

    operation_type, _ = lower_query("mutation { a }", variable_values={})
    assert operation_type == "mutation"

    operation_type, _ = lower_query("subscription { a }", variable_values={})
    assert operation_type == "subscription"


def test_skip_literal_true_prunes_field() -> None:
    _, result = lower_query("query { a b @skip(if: true) c }", variable_values={})
    assert field_names(result) == ["a", "c"]


def test_skip_literal_false_keeps_field() -> None:
    _, result = lower_query("query { a b @skip(if: false) c }", variable_values={})
    assert field_names(result) == ["a", "b", "c"]


def test_include_literal_false_prunes_field() -> None:
    _, result = lower_query("query { a b @include(if: false) c }", variable_values={})
    assert field_names(result) == ["a", "c"]


def test_include_literal_true_keeps_field() -> None:
    _, result = lower_query("query { a b @include(if: true) c }", variable_values={})
    assert field_names(result) == ["a", "b", "c"]


def test_skip_driven_by_variable() -> None:
    query = "query($shouldSkip: Boolean!) { a b @skip(if: $shouldSkip) c }"

    _, pruned = lower_query(query, variable_values={"shouldSkip": True})
    assert field_names(pruned) == ["a", "c"]

    _, kept = lower_query(query, variable_values={"shouldSkip": False})
    assert field_names(kept) == ["a", "b", "c"]


def test_include_driven_by_variable() -> None:
    query = "query($shouldInclude: Boolean!) { a b @include(if: $shouldInclude) c }"

    _, pruned = lower_query(query, variable_values={"shouldInclude": False})
    assert field_names(pruned) == ["a", "c"]

    _, kept = lower_query(query, variable_values={"shouldInclude": True})
    assert field_names(kept) == ["a", "b", "c"]


def test_nested_selection_pruning() -> None:
    query = "query($shouldSkip: Boolean!) { parent { inner1 inner2 @skip(if: $shouldSkip) } }"
    _, result = lower_query(query, variable_values={"shouldSkip": True})
    assert field_names(result) == ["parent"]
    assert field_names(result[0].selections) == ["inner1"]


def test_fragment_spread_is_flattened_and_pruned() -> None:
    query = """
    query($shouldSkip: Boolean!) {
        a
        ...frag
    }
    fragment frag on Query {
        b
        c @skip(if: $shouldSkip)
    }
    """
    _, result = lower_query(query, variable_values={"shouldSkip": True})
    assert field_names(result) == ["a", "b"]


def test_fragment_spread_itself_can_be_skipped() -> None:
    query = """
    query($shouldSkip: Boolean!) {
        a
        ...frag @skip(if: $shouldSkip)
    }
    fragment frag on Query {
        b
    }
    """
    _, result = lower_query(query, variable_values={"shouldSkip": True})
    assert field_names(result) == ["a"]


def test_inline_fragment_is_flattened_and_can_be_pruned() -> None:
    query = """
    query($shouldInclude: Boolean!) {
        a
        ... @include(if: $shouldInclude) {
            b
        }
    }
    """
    _, pruned = lower_query(query, variable_values={"shouldInclude": False})
    assert field_names(pruned) == ["a"]

    _, kept = lower_query(query, variable_values={"shouldInclude": True})
    assert field_names(kept) == ["a", "b"]


def test_undefined_variable_raises_graphql_error() -> None:
    with pytest.raises(bramble.GraphQLError) as excinfo:
        lower_query("query { a @skip(if: $missing) }", variable_values={})
    assert excinfo.value.code is bramble.ErrorCode.GRAPHQL_VALIDATION_FAILED


def test_non_boolean_variable_raises_graphql_error() -> None:
    with pytest.raises(bramble.GraphQLError):
        lower_query("query { a @skip(if: $x) }", variable_values={"x": "not a bool"})


def test_undefined_fragment_raises_graphql_error() -> None:
    with pytest.raises(bramble.GraphQLError):
        lower_query("query { ...missing }", variable_values={})


def test_multiple_operations_requires_operation_name() -> None:
    query = "query A { a } query B { b }"

    with pytest.raises(bramble.GraphQLError):
        lower_query(query, variable_values={})

    _, result = lower_query(query, variable_values={}, operation_name="B")
    assert field_names(result) == ["b"]


def test_parse_error_surfaces_as_graphql_error() -> None:
    with pytest.raises(bramble.GraphQLError) as excinfo:
        lower_query("{ a(", variable_values={})
    assert excinfo.value.code is bramble.ErrorCode.GRAPHQL_PARSE_FAILED


def test_response_key_reflects_alias() -> None:
    _, result = lower_query("query { renamed: a }", variable_values={})
    assert result[0].response_key == "renamed"
    assert result[0].field_name == "a"


def test_field_without_alias_has_matching_response_key() -> None:
    _, result = lower_query("query { a }", variable_values={})
    assert result[0].response_key == "a"
    assert result[0].field_name == "a"


def test_field_arguments_are_resolved_to_python_values() -> None:
    _, result = lower_query('query { a(count: 3, label: "hi") }', variable_values={})
    assert result[0].arguments == {"count": 3, "label": "hi"}


def test_field_arguments_resolve_variables() -> None:
    query = "query($n: Int!) { a(count: $n) }"
    _, result = lower_query(query, variable_values={"n": 42})
    assert result[0].arguments == {"count": 42}


def test_field_with_no_arguments_has_empty_dict() -> None:
    _, result = lower_query("query { a }", variable_values={})
    assert result[0].arguments == {}


def test_non_json_variable_value_still_resolves_as_a_field_argument() -> None:
    """A variable value that isn't JSON-representable (e.g. a `datetime`, or some custom scalar's
    own object) is fine as long as it's never used in `@skip`/`@include` -- only that boolean
    check needs a JSON-convertible map; field arguments resolve straight from the original Python
    variable values.
    """
    import datetime

    when = datetime.datetime(2024, 1, 1)
    query = "query($when: DateTime!) { a(when: $when) }"
    _, result = lower_query(query, variable_values={"when": when})
    assert result[0].arguments == {"when": when}


def test_custom_directive_is_preserved_with_resolved_arguments() -> None:
    query = '{ a @turnUppercase(shout: true) }'
    _, result = lower_query(query, variable_values={})
    assert len(result[0].directives) == 1
    directive = result[0].directives[0]
    assert directive.name == "turnUppercase"
    assert directive.arguments == {"shout": True}


def test_skip_and_include_directives_are_not_carried_to_execution() -> None:
    _, result = lower_query("query { a @include(if: true) }", variable_values={})
    assert result[0].directives == []


def test_inline_fragment_type_condition_is_preserved() -> None:
    query = """
    query {
        shape {
            ... on Circle {
                radius
            }
        }
    }
    """
    _, result = lower_query(query, variable_values={})
    shape_field = result[0]
    assert shape_field.type_condition is None
    radius_field = shape_field.selections[0]
    assert radius_field.field_name == "radius"
    assert radius_field.type_condition == "Circle"


def test_fragment_spread_type_condition_is_preserved() -> None:
    query = """
    query {
        shape {
            ...circleFields
        }
    }
    fragment circleFields on Circle {
        radius
    }
    """
    _, result = lower_query(query, variable_values={})
    radius_field = result[0].selections[0]
    assert radius_field.type_condition == "Circle"


def test_field_without_fragment_has_no_type_condition() -> None:
    _, result = lower_query("query { a }", variable_values={})
    assert result[0].type_condition is None


# --- @defer/@stream ------------------------------------------------------------------------------


def _by_response_key(fields, key: str):
    return next(f for f in fields if f.response_key == key)


def test_field_exclusive_to_a_deferred_fragment_is_marked_deferred() -> None:
    query = """
    query {
        id
        ... @defer(label: "extra") {
            name
        }
    }
    """
    _, result = lower_query(query, variable_values={})

    assert _by_response_key(result, "id").is_deferred is False
    name = _by_response_key(result, "name")
    assert name.is_deferred is True
    assert name.defer_label == "extra"


def test_deferred_field_with_no_label_has_none_label() -> None:
    _, result = lower_query("query { ... @defer { a } }", variable_values={})
    assert result[0].defer_label is None


def test_field_colliding_with_a_non_deferred_sibling_is_not_deferred() -> None:
    query = """
    query {
        a
        ... @defer {
            a
        }
    }
    """
    _, result = lower_query(query, variable_values={})
    assert all(f.is_deferred is False for f in result)


def test_defer_if_false_does_not_defer() -> None:
    _, result = lower_query("query { ... @defer(if: false) { a } }", variable_values={})
    assert result[0].is_deferred is False


def test_defer_if_driven_by_variable() -> None:
    query = "query($shouldDefer: Boolean!) { ... @defer(if: $shouldDefer) { a } }"
    _, deferred = lower_query(query, variable_values={"shouldDefer": True})
    assert deferred[0].is_deferred is True

    _, not_deferred = lower_query(query, variable_values={"shouldDefer": False})
    assert not_deferred[0].is_deferred is False


def test_deferred_fragment_spread_is_recognized() -> None:
    query = """
    query {
        id
        ...Extra @defer
    }
    fragment Extra on Query {
        name
    }
    """
    _, result = lower_query(query, variable_values={})
    assert _by_response_key(result, "id").is_deferred is False
    assert _by_response_key(result, "name").is_deferred is True


def test_stream_marker_on_a_field() -> None:
    _, result = lower_query('query { items @stream(initialCount: 2, label: "batch") }', variable_values={})
    items = result[0]
    assert items.is_streamed is True
    assert items.stream_initial_count == 2
    assert items.stream_label == "batch"


def test_stream_initial_count_defaults_to_zero() -> None:
    _, result = lower_query("query { items @stream }", variable_values={})
    assert result[0].stream_initial_count == 0


def test_stream_initial_count_driven_by_variable() -> None:
    query = "query($n: Int!) { items @stream(initialCount: $n) }"
    _, result = lower_query(query, variable_values={"n": 5})
    assert result[0].stream_initial_count == 5


def test_stream_if_false_does_not_stream() -> None:
    _, result = lower_query("query { items @stream(if: false) }", variable_values={})
    assert result[0].is_streamed is False


def test_field_with_neither_defer_nor_stream_has_both_unset() -> None:
    _, result = lower_query("query { a }", variable_values={})
    field_info = result[0]
    assert field_info.is_deferred is False
    assert field_info.defer_label is None
    assert field_info.is_streamed is False
    assert field_info.stream_initial_count is None
    assert field_info.stream_label is None


# --- Optional variables the caller omits ----------------------------------------------------------


@bramble.type
class _OptionalVariableQuery:
    @bramble.field
    def items(cursor: str | None = None, limit: int | None = 30) -> str:
        return f"cursor={cursor!r} limit={limit!r}"


_OPTIONAL_QUERY = "query Q($cursor: String, $limit: Int) { items(cursor: $cursor, limit: $limit) }"


def test_an_omitted_optional_variable_falls_back_to_the_arguments_default() -> None:
    """§CoerceArgumentValues: an argument whose variable has no supplied value is *omitted*, so the
    default applies. Treating it as an undefined variable instead made every ordinary paginating
    query fail -- `items(cursor: $cursor)` with no cursor is exactly what a client sends for the
    first page.
    """
    schema = bramble.Schema(query=_OptionalVariableQuery)

    result = schema.execute(_OPTIONAL_QUERY, variable_values={})

    assert result["data"] == {"items": "cursor=None limit=30"}


def test_an_explicit_null_is_distinct_from_an_omitted_variable() -> None:
    """Passing `null` means null; omitting means "use the default". Collapsing the two would make
    `limit` unclearable.
    """
    schema = bramble.Schema(query=_OptionalVariableQuery)

    result = schema.execute(_OPTIONAL_QUERY, variable_values={"cursor": None, "limit": None})

    assert result["data"] == {"items": "cursor=None limit=None"}


def test_supplied_variables_are_still_passed_through() -> None:
    schema = bramble.Schema(query=_OptionalVariableQuery)

    result = schema.execute(_OPTIONAL_QUERY, variable_values={"cursor": "abc", "limit": 5})

    assert result["data"] == {"items": "cursor='abc' limit=5"}
