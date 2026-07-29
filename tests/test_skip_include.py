from __future__ import annotations

import pytest

import bramble
from bramble._bramble import prune_selections


def field_names(fields) -> list[str]:
    return [f.name for f in fields]


def test_prune_selections_with_no_directives_keeps_everything() -> None:
    result = prune_selections("query { a b c }", variable_values={})
    assert field_names(result) == ["a", "b", "c"]


def test_skip_literal_true_prunes_field() -> None:
    result = prune_selections("query { a b @skip(if: true) c }", variable_values={})
    assert field_names(result) == ["a", "c"]


def test_skip_literal_false_keeps_field() -> None:
    result = prune_selections("query { a b @skip(if: false) c }", variable_values={})
    assert field_names(result) == ["a", "b", "c"]


def test_include_literal_false_prunes_field() -> None:
    result = prune_selections("query { a b @include(if: false) c }", variable_values={})
    assert field_names(result) == ["a", "c"]


def test_include_literal_true_keeps_field() -> None:
    result = prune_selections("query { a b @include(if: true) c }", variable_values={})
    assert field_names(result) == ["a", "b", "c"]


def test_skip_driven_by_variable() -> None:
    query = "query($shouldSkip: Boolean!) { a b @skip(if: $shouldSkip) c }"

    pruned = prune_selections(query, variable_values={"shouldSkip": True})
    assert field_names(pruned) == ["a", "c"]

    kept = prune_selections(query, variable_values={"shouldSkip": False})
    assert field_names(kept) == ["a", "b", "c"]


def test_include_driven_by_variable() -> None:
    query = "query($shouldInclude: Boolean!) { a b @include(if: $shouldInclude) c }"

    pruned = prune_selections(query, variable_values={"shouldInclude": False})
    assert field_names(pruned) == ["a", "c"]

    kept = prune_selections(query, variable_values={"shouldInclude": True})
    assert field_names(kept) == ["a", "b", "c"]


def test_nested_selection_pruning() -> None:
    query = "query($shouldSkip: Boolean!) { parent { inner1 inner2 @skip(if: $shouldSkip) } }"
    result = prune_selections(query, variable_values={"shouldSkip": True})
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
    result = prune_selections(query, variable_values={"shouldSkip": True})
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
    result = prune_selections(query, variable_values={"shouldSkip": True})
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
    pruned = prune_selections(query, variable_values={"shouldInclude": False})
    assert field_names(pruned) == ["a"]

    kept = prune_selections(query, variable_values={"shouldInclude": True})
    assert field_names(kept) == ["a", "b"]


def test_undefined_variable_raises_graphql_error() -> None:
    with pytest.raises(bramble.GraphQLError) as excinfo:
        prune_selections("query { a @skip(if: $missing) }", variable_values={})
    assert excinfo.value.code is bramble.ErrorCode.GRAPHQL_VALIDATION_FAILED


def test_non_boolean_variable_raises_graphql_error() -> None:
    with pytest.raises(bramble.GraphQLError):
        prune_selections("query { a @skip(if: $x) }", variable_values={"x": "not a bool"})


def test_undefined_fragment_raises_graphql_error() -> None:
    with pytest.raises(bramble.GraphQLError):
        prune_selections("query { ...missing }", variable_values={})


def test_multiple_operations_requires_operation_name() -> None:
    query = "query A { a } query B { b }"

    with pytest.raises(bramble.GraphQLError):
        prune_selections(query, variable_values={})

    result = prune_selections(query, variable_values={}, operation_name="B")
    assert field_names(result) == ["b"]


def test_parse_error_surfaces_as_graphql_error() -> None:
    with pytest.raises(bramble.GraphQLError) as excinfo:
        prune_selections("{ a(", variable_values={})
    assert excinfo.value.code is bramble.ErrorCode.GRAPHQL_PARSE_FAILED
