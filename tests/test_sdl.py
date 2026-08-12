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
import re
from typing import Annotated, NewType, Union

import pytest

import bramble
from bramble.directive import DirectiveLocation, DirectiveValue
from bramble.schema.config import SchemaConfig
from bramble.schema_directive import Location

# `typing.get_type_hints` can only see module globals, never an enclosing test function's locals
# (see test_schema.py's own comment on this) -- anything referenced *from another annotation*
# (a field type, a directive's own DirectiveValue[T] parameter) has to live at module level here.


@bramble.type
class _Audio:
    title: str


@bramble.type
class _Video:
    title: str


MediaItem = Annotated[Union[_Audio, _Video], bramble.union("MediaItem")]

Base64 = NewType("Base64", bytes)


@bramble.directive(locations=[DirectiveLocation.FIELD])
def turn_uppercase(value: DirectiveValue[str]) -> str:
    return value.upper()


def test_to_sdl_renders_schema_block_and_object_type() -> None:
    @bramble.type
    class Query:
        @bramble.field
        def greet(name: str) -> str:
            return f"hi {name}"

    schema = bramble.Schema(query=Query)
    sdl = schema.to_sdl()

    assert "schema {\n  query: Query\n}" in sdl
    assert "type Query {\n  greet(name: String!): String!\n}" in sdl


def test_str_of_schema_matches_to_sdl() -> None:
    @bramble.type
    class Query:
        hello: str

    schema = bramble.Schema(query=Query)

    assert str(schema) == schema.to_sdl()


def test_to_sdl_renders_type_description() -> None:
    @bramble.type(description="The root query type")
    class Query:
        hello: str

    schema = bramble.Schema(query=Query)

    assert '"""The root query type"""\ntype Query' in schema.to_sdl()


def test_to_sdl_renders_field_description() -> None:
    @bramble.type
    class Query:
        hello: str = bramble.field(description="a greeting", default="hi")

    schema = bramble.Schema(query=Query)

    assert '"""a greeting"""\n  hello: String!' in schema.to_sdl()


def test_to_sdl_renders_field_name_override() -> None:
    @bramble.type
    class Query:
        internal: str = bramble.field(name="publicName", default="x")

    schema = bramble.Schema(query=Query)

    assert "publicName: String!" in schema.to_sdl()
    assert "internal:" not in schema.to_sdl()


def test_to_sdl_renders_applied_type_level_directive_with_arguments() -> None:
    @bramble.schema_directive(locations=[Location.OBJECT])
    class Keys:
        fields: str

    @bramble.type(directives=[Keys(fields="id")])
    class User:
        id: str

    @bramble.type
    class Query:
        user: User

    schema = bramble.Schema(query=Query, types=[User])
    sdl = schema.to_sdl()

    assert 'type User @keys(fields: "id") {' in sdl
    assert "directive @keys(fields: String!) on OBJECT" in sdl


def test_to_sdl_renders_applied_field_level_directive() -> None:
    @bramble.schema_directive(locations=[Location.FIELD_DEFINITION])
    class Deprecated:
        reason: str

    @bramble.type
    class Query:
        old: str = bramble.field(directives=[Deprecated(reason="use new")], default="x")

    schema = bramble.Schema(query=Query)
    sdl = schema.to_sdl()

    assert 'old: String! @deprecated(reason: "use new")' in sdl
    assert "directive @deprecated(reason: String!) on FIELD_DEFINITION" in sdl


def test_to_sdl_renders_schema_level_applied_directive_and_repeatable_declaration() -> None:
    @bramble.schema_directive(locations=[Location.SCHEMA], repeatable=True)
    class Link:
        url: str

    @bramble.type
    class Query:
        greet: str

    schema = bramble.Schema(query=Query, schema_directives=[Link(url="https://example.com/spec")])
    sdl = schema.to_sdl()

    assert 'schema @link(url: "https://example.com/spec") {' in sdl
    assert "directive @link(url: String!) repeatable on SCHEMA" in sdl


def test_schema_directives_rejects_a_non_directive_instance() -> None:
    @bramble.type
    class Query:
        greet: str

    with pytest.raises(bramble.SchemaError, match=re.escape("not a @bramble.schema_directive instance")):
        bramble.Schema(query=Query, schema_directives=[object()])


def test_to_sdl_renders_union() -> None:
    @bramble.type
    class Query:
        media: MediaItem

    schema = bramble.Schema(query=Query, types=[_Audio, _Video])

    assert "union MediaItem = _Audio | _Video" in schema.to_sdl()


def test_to_sdl_renders_custom_scalar() -> None:
    @bramble.type
    class Query:
        @bramble.field
        def data() -> Base64:
            return b""

    schema = bramble.Schema(query=Query, config=SchemaConfig(scalar_map={Base64: bramble.scalar(name="Base64")}))

    assert "scalar Base64" in schema.to_sdl()


def test_to_sdl_renders_custom_scalar_description() -> None:
    @bramble.type
    class Query:
        @bramble.field
        def data() -> Base64:
            return b""

    schema = bramble.Schema(
        query=Query,
        config=SchemaConfig(scalar_map={Base64: bramble.scalar(name="Base64", description="Base64 bytes")}),
    )

    assert '"""Base64 bytes"""\nscalar Base64' in schema.to_sdl()


def test_to_sdl_renders_operation_directive() -> None:
    @bramble.type
    class Query:
        hello: str

    schema = bramble.Schema(query=Query, directives=[turn_uppercase])

    assert "directive @turnUppercase on FIELD" in schema.to_sdl()


def test_to_sdl_renders_mutation_in_schema_block() -> None:
    @bramble.type
    class Query:
        hello: str

    @bramble.type
    class Mutation:
        @bramble.field
        def noop() -> str:
            return "noop"

    schema = bramble.Schema(query=Query, mutation=Mutation)

    assert "schema {\n  query: Query\n  mutation: Mutation\n}" in schema.to_sdl()


# Printer scenarios: built-in scalar rendering, interface implements-clauses, and root types with
# custom names.


def test_to_sdl_renders_all_builtin_scalar_types_as_non_null() -> None:
    @bramble.type
    class Query:
        a: str
        b: int
        c: bool
        d: float
        e: bramble.ID

    schema = bramble.Schema(query=Query)
    sdl = schema.to_sdl()

    assert "a: String!" in sdl
    assert "b: Int!" in sdl
    assert "c: Boolean!" in sdl
    assert "d: Float!" in sdl
    assert "e: ID!" in sdl


def test_to_sdl_renders_optional_scalar_as_nullable() -> None:
    @bramble.type
    class Query:
        maybe: str | None

    schema = bramble.Schema(query=Query)

    assert "maybe: String\n" in schema.to_sdl()
    assert "maybe: String!" not in schema.to_sdl()


def test_to_sdl_renders_implements_clause_for_an_interface() -> None:
    @bramble.interface
    class Node:
        @bramble.field
        def id() -> bramble.ID:
            return "1"

    @bramble.type
    class User(Node):
        @bramble.field
        def id(parent: bramble.Parent[object]) -> bramble.ID:
            return "1"

    schema = bramble.Schema(query=User, types=[User])  # abusing User as a throwaway root
    sdl = schema.to_sdl()

    assert "type User implements Node {" in sdl


def test_to_sdl_renders_implements_clause_for_transitive_interface_inheritance() -> None:
    @bramble.interface
    class Node:
        @bramble.field
        def id() -> bramble.ID:
            return "1"

    @bramble.interface
    class Timestamped(Node):
        @bramble.field
        def created_at() -> str:
            return "now"

    @bramble.type
    class User(Timestamped):
        @bramble.field
        def id(parent: bramble.Parent[object]) -> bramble.ID:
            return "1"

        @bramble.field
        def created_at(parent: bramble.Parent[object]) -> str:
            return "now"

    schema = bramble.Schema(query=User, types=[User])
    sdl = schema.to_sdl()

    assert "interface Timestamped implements Node {" in sdl
    assert "type User implements Timestamped & Node {" in sdl


def test_to_sdl_renders_root_type_with_custom_name_in_schema_block() -> None:
    @bramble.type(name="RootQuery")
    class Query:
        @bramble.field
        def x() -> int:
            return 1

    schema = bramble.Schema(query=Query)
    sdl = schema.to_sdl()

    assert "schema {\n  query: RootQuery\n}" in sdl
    assert "type RootQuery {" in sdl


# --- Argument defaults ----------------------------------------------------------------------------


def test_to_sdl_renders_argument_defaults_so_they_do_not_read_as_required() -> None:
    """A non-null argument with a Python default is optional to the server, and the SDL has to say
    so: `Int!` on its own means *required* to every spec-compliant consumer, which used to make the
    published schema disagree with what the server actually accepts.
    """

    @bramble.type
    class Query:
        @bramble.field
        def search(limit: int = 10, term: str = "all", exact: bool = False, ratio: float = 0.5) -> str:
            return term

    sdl = bramble.Schema(query=Query).to_sdl()

    assert 'search(limit: Int! = 10, term: String! = "all", exact: Boolean! = false, ratio: Float! = 0.5)' in sdl


@bramble.enum
class _DefaultColor(enum.Enum):
    """A `str`-valued enum on purpose: it is a genuine `str` subclass, so it is exactly the case
    that would render as the string literal `"red"` if the enum branch weren't checked first.
    """

    RED = "red"
    GREEN = "green"


@bramble.enum
class _RenamedColor(enum.Enum):
    RED = bramble.enum_value("red", name="CRIMSON")


def test_to_sdl_renders_an_enum_argument_default_as_an_enum_literal_not_a_string() -> None:
    @bramble.type
    class Query:
        @bramble.field
        def pick(color: _DefaultColor = _DefaultColor.RED) -> _DefaultColor:
            return color

    sdl = bramble.Schema(query=Query).to_sdl()

    assert "pick(color: _DefaultColor! = RED): _DefaultColor!" in sdl


def test_to_sdl_renders_a_renamed_enum_members_default_under_its_graphql_name() -> None:
    @bramble.type
    class Query:
        @bramble.field
        def pick(color: _RenamedColor = _RenamedColor.RED) -> _RenamedColor:
            return color

    assert "pick(color: _RenamedColor! = CRIMSON)" in bramble.Schema(query=Query).to_sdl()


def test_to_sdl_omits_a_default_with_no_faithful_graphql_literal() -> None:
    """An unrepresentable default renders nothing rather than something wrong -- the argument stays
    optional at execution either way.
    """

    class Sentinel:
        pass

    @bramble.type
    class Query:
        @bramble.field
        def search(cursor: str | None = Sentinel()) -> str:  # type: ignore[assignment]
            return "ok"

    sdl = bramble.Schema(query=Query).to_sdl()

    assert "search(cursor: String): String!" in sdl
    assert "Sentinel" not in sdl


def test_argument_default_lists_and_nulls_render_as_graphql_literals() -> None:
    @bramble.type
    class Query:
        @bramble.field
        def search(tags: list[str] = ["a", "b"], cursor: str | None = None) -> str:  # noqa: B006
            return "ok"

    sdl = bramble.Schema(query=Query).to_sdl()

    assert 'search(tags: [String!]! = ["a", "b"], cursor: String = null)' in sdl


def test_to_sdl_renders_argument_descriptions_inline() -> None:
    """Inline rather than on their own line: arguments print comma-separated inside one `(...)`,
    so a line break would split the argument list mid-expression.
    """

    @bramble.type
    class Query:
        @bramble.field
        def greet(name: Annotated[str, bramble.argument(description="Who to greet")]) -> str:
            return name

    assert 'greet("""Who to greet""" name: String!): String!' in bramble.Schema(query=Query).to_sdl()
