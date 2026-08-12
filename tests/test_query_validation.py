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

import re
import subprocess
import sys

import pytest

import bramble
from bramble.directive import DirectiveLocation, DirectiveValue


@bramble.type
class Author:
    name: str


@bramble.type
class Query:
    author: Author
    tags: list[str]

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


# --- @defer/@stream ------------------------------------------------------------------------------


def test_defer_on_an_inline_fragment_passes() -> None:
    _schema().validate_query("query { ... @defer(label: \"extra\") { author { name } } }")


def test_defer_on_a_fragment_spread_passes() -> None:
    _schema().validate_query(
        'query { ...Extra @defer(label: "extra") } fragment Extra on Query { author { name } }'
    )


def test_defer_on_a_field_fails() -> None:
    schema = _schema()
    with pytest.raises(bramble.GraphQLError) as excinfo:
        schema.validate_query("query { author @defer { name } }")

    assert excinfo.value.code is bramble.ErrorCode.INVALID_DIRECTIVE_LOCATION


def test_defer_with_a_non_string_label_fails() -> None:
    schema = _schema()
    with pytest.raises(bramble.GraphQLError) as excinfo:
        schema.validate_query("query { ... @defer(label: 123) { author { name } } }")

    assert excinfo.value.code is bramble.ErrorCode.ARGUMENT_TYPE_MISMATCH


def test_defer_with_an_unknown_argument_fails() -> None:
    schema = _schema()
    with pytest.raises(bramble.GraphQLError) as excinfo:
        schema.validate_query('query { ... @defer(bogus: "x") { author { name } } }')

    assert excinfo.value.code is bramble.ErrorCode.UNKNOWN_ARGUMENT


def test_stream_on_a_list_field_passes() -> None:
    _schema().validate_query("query { tags @stream(initialCount: 1) }")


def test_stream_on_a_non_list_field_fails() -> None:
    schema = _schema()
    with pytest.raises(bramble.GraphQLError) as excinfo:
        schema.validate_query("query { author @stream { name } }")

    assert excinfo.value.code is bramble.ErrorCode.INVALID_DIRECTIVE_LOCATION
    assert "@stream" in excinfo.value.message


def test_stream_with_a_non_integer_initial_count_fails() -> None:
    schema = _schema()
    with pytest.raises(bramble.GraphQLError) as excinfo:
        schema.validate_query('query { tags @stream(initialCount: "one") }')

    assert excinfo.value.code is bramble.ErrorCode.ARGUMENT_TYPE_MISMATCH


def test_stream_on_a_fragment_spread_fails() -> None:
    schema = _schema()
    with pytest.raises(bramble.GraphQLError) as excinfo:
        schema.validate_query('query { ...Extra @stream } fragment Extra on Query { tags }')

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


# --- Fragment cycles -------------------------------------------------------------------------
#
# A cyclic fragment spread used to send both the Rust validator and the lowering pass into an
# unbounded loop, hanging the worker on any unauthenticated request. These run in a *subprocess*
# with a hard timeout rather than in-process: the Rust bindings hold the GIL for the whole call,
# so a regression would freeze the whole pytest run (including any in-process watchdog thread)
# rather than failing. A subprocess is the only thing that can still be killed.

_CYCLE_PROBE = """
import bramble

@bramble.type
class Author:
    name: str

@bramble.type
class Query:
    author: Author

schema = bramble.Schema(query=Query, types=[Author])
try:
    schema.{method}({args})
except bramble.GraphQLError as error:
    print("ERROR:" + error.message)
else:
    print("NO_ERROR")
"""


def _run_probe(method: str, args: str, timeout: float = 30.0) -> str:
    completed = subprocess.run(
        [sys.executable, "-c", _CYCLE_PROBE.format(method=method, args=args)],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=True,
    )
    return completed.stdout.strip()


@pytest.mark.parametrize(
    ("label", "query"),
    [
        ("self reference", "query { author { ...A } } fragment A on Author { name ...A }"),
        (
            "mutual recursion",
            "query { author { ...A } } fragment A on Author { ...B } fragment B on Author { ...A }",
        ),
        (
            "cycle behind an inline fragment",
            "query { author { ...A } } fragment A on Author { ... on Author { ...A } }",
        ),
    ],
)
def test_cyclic_fragments_are_rejected_by_validation_without_hanging(label: str, query: str) -> None:
    output = _run_probe("validate_query", repr(query))
    assert output.startswith("ERROR:"), f"{label}: expected a validation error, got {output!r}"
    assert "Fragment cycle detected" in output, f"{label}: unexpected message {output!r}"


def test_cyclic_fragments_are_rejected_by_lowering_without_hanging() -> None:
    # `execute_async` validates first, but the HTTP view's `@defer`/`@stream` peek and the
    # WebSocket handler both lower *unvalidated* input -- so lowering needs its own guard, and
    # this exercises the path that reaches it.
    query = "query { author { ...A } } fragment A on Author { ...A }"
    output = _run_probe("execute", repr(query))
    assert output.startswith("ERROR:"), f"expected an error, got {output!r}"
    assert "Fragment cycle detected" in output


def test_repeated_non_cyclic_fragment_spreads_are_still_accepted() -> None:
    schema = _schema()
    schema.validate_query(
        "query { first: author { ...frag } second: author { ...frag } } fragment frag on Author { name }"
    )


# --- Leaf / composite selection sets ----------------------------------------------------------


def test_selection_set_on_a_scalar_field_is_rejected() -> None:
    schema = _schema()
    with pytest.raises(bramble.GraphQLError, match="leaf type 'String'"):
        schema.validate_query("query { author { name { length } } }")


def test_composite_field_without_a_selection_set_is_rejected() -> None:
    schema = _schema()
    with pytest.raises(bramble.GraphQLError, match="composite type 'Author'"):
        schema.validate_query("query { author }")


# --- Fragment spread possibility --------------------------------------------------------------


def test_fragment_on_an_unrelated_type_is_rejected() -> None:
    schema = _schema()
    with pytest.raises(bramble.GraphQLError, match="can never apply to"):
        schema.validate_query("query { author { ...q } } fragment q on Query { tags }")


def test_inline_fragment_on_an_unrelated_type_is_rejected() -> None:
    schema = _schema()
    with pytest.raises(bramble.GraphQLError, match="can never apply to"):
        schema.validate_query("query { author { ... on Query { tags } } }")


# --- Uniqueness -------------------------------------------------------------------------------


def test_repeated_argument_on_a_field_is_rejected() -> None:
    schema = _schema()
    with pytest.raises(bramble.GraphQLError, match="provided more than once"):
        schema.validate_query('query { greet(name: "Ada", name: "Grace") }')


def test_repeated_variable_declaration_is_rejected() -> None:
    schema = _schema()
    with pytest.raises(bramble.GraphQLError, match=r"'\$n' is declared more than once"):
        schema.validate_query("query Q($n: String!, $n: String!) { greet(name: $n) }")


# --- Variable usage types ----------------------------------------------------------------------


def test_variable_of_the_wrong_type_is_rejected() -> None:
    schema = _schema()
    with pytest.raises(bramble.GraphQLError, match="is declared as 'Int!'"):
        schema.validate_query("query Q($n: Int!) { greet(name: $n) }")


def test_undeclared_variable_usage_is_rejected() -> None:
    schema = _schema()
    with pytest.raises(bramble.GraphQLError, match=re.escape("undefined variable '$missing'")):
        schema.validate_query("query Q { greet(name: $missing) }")


def test_correctly_typed_variable_is_accepted() -> None:
    _schema().validate_query("query Q($n: String!) { greet(name: $n) }")


def test_nullable_variable_is_allowed_where_the_argument_has_a_default() -> None:
    # `greet(shout: bool = False)` has a default, so a nullable variable is permitted there.
    _schema().validate_query('query Q($s: Boolean) { greet(name: "Ada", shout: $s) }')


def test_duplicate_operation_names_are_rejected_by_the_parser() -> None:
    """Recorded during the audit as unfixable because the parser stores operations in a `HashMap`.
    That inference was wrong: it rejects the redefinition while building the document, so this
    surfaces as a parse error rather than a silent last-one-wins.
    """
    schema = _schema()
    with pytest.raises(bramble.GraphQLError) as excinfo:
        schema.validate_query("query A { tags } query A { tags }", operation_name="A")

    assert excinfo.value.code is bramble.ErrorCode.GRAPHQL_PARSE_FAILED


def test_duplicate_fragment_names_are_rejected_by_the_parser() -> None:
    schema = _schema()
    with pytest.raises(bramble.GraphQLError) as excinfo:
        schema.validate_query("query { ...F } fragment F on Query { tags } fragment F on Query { tags }")

    assert excinfo.value.code is bramble.ErrorCode.GRAPHQL_PARSE_FAILED
