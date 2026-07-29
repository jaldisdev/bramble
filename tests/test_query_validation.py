from __future__ import annotations

import pytest

import bramble
from bramble.directive import DirectiveLocation, DirectiveValue


@bramble.type
class Author:
    name: str


@bramble.type
class Query:
    author: Author

    @bramble.field
    def greet(name: str, shout: bool = False) -> str:
        return name


def _schema(**kwargs: object) -> bramble.Schema:
    return bramble.Schema(query=Query, types=[Author], **kwargs)


def test_valid_query_passes() -> None:
    schema = _schema()
    schema.validate_query('query { greet(name: "Ada") }')
    schema.validate_query("query { author { name } }")


def test_unknown_field_fails_with_correct_error() -> None:
    schema = _schema()
    with pytest.raises(bramble.GraphQLError) as excinfo:
        schema.validate_query("query { doesNotExist }")

    assert excinfo.value.code is bramble.ErrorCode.UNKNOWN_FIELD
    assert "doesNotExist" in excinfo.value.message
    assert excinfo.value.locations is not None


def test_unknown_nested_field_fails() -> None:
    schema = _schema()
    with pytest.raises(bramble.GraphQLError) as excinfo:
        schema.validate_query("query { author { doesNotExist } }")

    assert excinfo.value.code is bramble.ErrorCode.UNKNOWN_FIELD
    assert "Author" in excinfo.value.message


def test_wrong_argument_type_fails() -> None:
    schema = _schema()
    with pytest.raises(bramble.GraphQLError) as excinfo:
        schema.validate_query("query { greet(name: 123) }")

    assert excinfo.value.code is bramble.ErrorCode.ARGUMENT_TYPE_MISMATCH


def test_missing_required_argument_fails() -> None:
    schema = _schema()
    with pytest.raises(bramble.GraphQLError) as excinfo:
        schema.validate_query("query { greet }")

    assert excinfo.value.code is bramble.ErrorCode.ARGUMENT_TYPE_MISMATCH
    assert "name" in excinfo.value.message


def test_unknown_argument_fails() -> None:
    schema = _schema()
    with pytest.raises(bramble.GraphQLError) as excinfo:
        schema.validate_query('query { greet(name: "Ada", extra: 1) }')

    assert excinfo.value.code is bramble.ErrorCode.UNKNOWN_ARGUMENT


def test_optional_argument_may_be_omitted() -> None:
    _schema().validate_query('query { greet(name: "Ada") }')


def test_boolean_argument_accepts_boolean_literal() -> None:
    _schema().validate_query('query { greet(name: "Ada", shout: true) }')


def test_boolean_argument_rejects_non_boolean() -> None:
    schema = _schema()
    with pytest.raises(bramble.GraphQLError) as excinfo:
        schema.validate_query('query { greet(name: "Ada", shout: "yes") }')

    assert excinfo.value.code is bramble.ErrorCode.ARGUMENT_TYPE_MISMATCH


def test_unknown_directive_fails() -> None:
    schema = _schema()
    with pytest.raises(bramble.GraphQLError) as excinfo:
        schema.validate_query('query { greet(name: "Ada") @madeUpDirective }')

    assert excinfo.value.code is bramble.ErrorCode.INVALID_DIRECTIVE_LOCATION


def test_directive_at_disallowed_location_fails() -> None:
    @bramble.directive(locations=[DirectiveLocation.QUERY])
    def query_only(value: DirectiveValue[str]) -> str:
        return value

    schema = _schema(directives=[query_only])

    with pytest.raises(bramble.GraphQLError) as excinfo:
        schema.validate_query('query { greet(name: "Ada") @queryOnly }')

    assert excinfo.value.code is bramble.ErrorCode.INVALID_DIRECTIVE_LOCATION


def test_directive_at_allowed_location_passes() -> None:
    @bramble.directive(locations=[DirectiveLocation.FIELD])
    def turn_uppercase(value: DirectiveValue[str]) -> str:
        return value.upper()

    schema = _schema(directives=[turn_uppercase])
    schema.validate_query('query { greet(name: "Ada") @turnUppercase }')


def test_skip_and_include_are_always_legal_without_registration() -> None:
    schema = _schema()
    schema.validate_query('query($x: Boolean!) { greet(name: "Ada") @skip(if: $x) }')
    schema.validate_query('query($x: Boolean!) { greet(name: "Ada") @include(if: $x) }')


def test_invalid_fragment_spread_target_fails() -> None:
    schema = _schema()
    with pytest.raises(bramble.GraphQLError) as excinfo:
        schema.validate_query('query { ...frag } fragment frag on NotARealType { greet(name: "Ada") }')

    assert excinfo.value.code is bramble.ErrorCode.INVALID_FRAGMENT_TARGET


def test_undefined_fragment_spread_fails() -> None:
    schema = _schema()
    with pytest.raises(bramble.GraphQLError) as excinfo:
        schema.validate_query("query { ...missing }")

    assert excinfo.value.code is bramble.ErrorCode.INVALID_FRAGMENT_TARGET


def test_invalid_inline_fragment_target_fails() -> None:
    schema = _schema()
    with pytest.raises(bramble.GraphQLError) as excinfo:
        schema.validate_query('query { ... on NotARealType { greet(name: "Ada") } }')

    assert excinfo.value.code is bramble.ErrorCode.INVALID_FRAGMENT_TARGET


def test_valid_fragment_spread_passes() -> None:
    schema = _schema()
    schema.validate_query("query { ...frag } fragment frag on Query { author { name } }")


def test_valid_inline_fragment_passes() -> None:
    schema = _schema()
    schema.validate_query("query { ... on Query { author { name } } }")


def test_typename_meta_field_is_always_valid() -> None:
    _schema().validate_query("query { __typename }")


def test_parse_error_surfaces_as_graphql_error() -> None:
    schema = _schema()
    with pytest.raises(bramble.GraphQLError) as excinfo:
        schema.validate_query("query { greet(")

    assert excinfo.value.code is bramble.ErrorCode.GRAPHQL_PARSE_FAILED
