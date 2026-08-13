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

import datetime
import decimal
import enum
import json
import uuid

import bramble
import bramble.federation as federation
from bramble.directive import DirectiveLocation, DirectiveValue

# The introspection query GraphiQL/graphql-js actually sends on load (`getIntrospectionQuery()`).
# Kept verbatim rather than trimmed: the point of the end-to-end test below is that the *real*
# client query works, including every fragment and the `includeDeprecated` arguments.
INTROSPECTION_QUERY = """
query IntrospectionQuery {
  __schema {
    queryType { name }
    mutationType { name }
    subscriptionType { name }
    types { ...FullType }
    directives { name description locations args { ...InputValue } }
  }
}
fragment FullType on __Type {
  kind name description
  fields(includeDeprecated: true) {
    name description
    args { ...InputValue }
    type { ...TypeRef }
    isDeprecated deprecationReason
  }
  inputFields { ...InputValue }
  interfaces { ...TypeRef }
  enumValues(includeDeprecated: true) { name description isDeprecated deprecationReason }
  possibleTypes { ...TypeRef }
}
fragment InputValue on __InputValue { name description type { ...TypeRef } defaultValue }
fragment TypeRef on __Type {
  kind name
  ofType { kind name ofType { kind name ofType { kind name } } }
}
"""


@bramble.enum
class Colour(enum.Enum):
    RED = "red"
    GREEN = bramble.enum_value("green", description="Green things")
    OLD = bramble.enum_value("old", deprecation_reason="no longer used")


@bramble.interface
class Node:
    @bramble.field
    def id(parent: bramble.Parent[object]) -> bramble.ID:
        return bramble.ID("1")


@bramble.type
class Item(Node):
    @bramble.field
    def name(parent: bramble.Parent[object]) -> str:
        return "an item"


@bramble.input
class ItemFilter:
    colour: Colour
    limit: int | None = None


@bramble.directive(locations=[DirectiveLocation.FIELD], description="Shouts a value")
def shout(value: DirectiveValue[str]) -> str:
    return value.upper()


@bramble.type
class Query:
    @bramble.field(description="Every item")
    def items(filter: ItemFilter | None = None) -> list[Item]:
        return [Item()]

    @bramble.field
    def node() -> Node | None:
        return Item()


@federation.type(keys=["id"])
class Product:
    id: str

    @classmethod
    def resolve_reference(cls, id: str) -> "Product":
        return cls(id=id)


@bramble.type
class FederatedQuery:
    @bramble.field
    def product() -> Product:
        return Product(id="1")


def _schema() -> bramble.Schema:
    return bramble.Schema(query=Query, types=[Item], directives=[shout])


def _types_by_name(result: dict) -> dict:
    return {entry["name"]: entry for entry in result["data"]["__schema"]["types"]}


# --- The real client query ------------------------------------------------------------------------


def test_full_graphiql_introspection_query_succeeds() -> None:
    result = _schema().execute(INTROSPECTION_QUERY)

    assert "errors" not in result, result.get("errors")
    json.dumps(result)  # a client receives this over the wire; it must serialize


def test_root_operation_types_are_reported() -> None:
    schema_field = _schema().execute(INTROSPECTION_QUERY)["data"]["__schema"]

    assert schema_field["queryType"] == {"name": "Query"}
    assert schema_field["mutationType"] is None
    assert schema_field["subscriptionType"] is None


# --- __type ---------------------------------------------------------------------------------------


def test_type_lookup_by_name() -> None:
    result = _schema().execute('{ __type(name: "Item") { name kind } }')
    assert result == {"data": {"__type": {"name": "Item", "kind": "OBJECT"}}}


def test_type_lookup_of_an_unknown_name_is_null() -> None:
    result = _schema().execute('{ __type(name: "NoSuchType") { name } }')
    assert result == {"data": {"__type": None}}


def test_builtin_scalars_are_introspectable() -> None:
    result = _schema().execute('{ __type(name: "String") { name kind } }')
    assert result == {"data": {"__type": {"name": "String", "kind": "SCALAR"}}}


# --- Type shapes ------------------------------------------------------------------------------------


def test_wrapper_types_nest_through_of_type() -> None:
    """`[Item!]!` has to come back as NON_NULL -> LIST -> NON_NULL -> OBJECT, which is what a
    client walks to reconstruct the type.
    """
    types = _types_by_name(_schema().execute(INTROSPECTION_QUERY))
    items = next(field for field in types["Query"]["fields"] if field["name"] == "items")

    assert items["type"] == {
        "kind": "NON_NULL",
        "name": None,
        "ofType": {
            "kind": "LIST",
            "name": None,
            "ofType": {"kind": "NON_NULL", "name": None, "ofType": {"kind": "OBJECT", "name": "Item"}},
        },
    }


def test_object_reports_its_interfaces_and_interface_reports_possible_types() -> None:
    types = _types_by_name(_schema().execute(INTROSPECTION_QUERY))

    assert [entry["name"] for entry in types["Item"]["interfaces"]] == ["Node"]
    assert types["Node"]["kind"] == "INTERFACE"
    assert [entry["name"] for entry in types["Node"]["possibleTypes"]] == ["Item"]


def test_input_object_reports_its_input_fields() -> None:
    types = _types_by_name(_schema().execute(INTROSPECTION_QUERY))
    fields = {entry["name"]: entry for entry in types["ItemFilter"]["inputFields"]}

    assert types["ItemFilter"]["kind"] == "INPUT_OBJECT"
    assert fields["colour"]["type"] == {
        "kind": "NON_NULL",
        "name": None,
        "ofType": {"kind": "ENUM", "name": "Colour", "ofType": None},
    }


def test_enum_reports_its_values_and_deprecation() -> None:
    types = _types_by_name(_schema().execute(INTROSPECTION_QUERY))
    values = {entry["name"]: entry for entry in types["Colour"]["enumValues"]}

    assert types["Colour"]["kind"] == "ENUM"
    assert values["GREEN"]["description"] == "Green things"
    assert values["OLD"]["isDeprecated"] is True
    assert values["OLD"]["deprecationReason"] == "no longer used"


def test_include_deprecated_false_filters_deprecated_enum_values() -> None:
    result = _schema().execute('{ __type(name: "Colour") { enumValues { name } } }')
    names = [entry["name"] for entry in result["data"]["__type"]["enumValues"]]

    assert "OLD" not in names
    assert names == ["RED", "GREEN"]


def test_field_description_is_reported() -> None:
    types = _types_by_name(_schema().execute(INTROSPECTION_QUERY))
    items = next(field for field in types["Query"]["fields"] if field["name"] == "items")

    assert items["description"] == "Every item"


def test_custom_operation_directive_is_reported() -> None:
    directives = {
        entry["name"]: entry for entry in _schema().execute(INTROSPECTION_QUERY)["data"]["__schema"]["directives"]
    }

    assert directives["shout"]["description"] == "Shouts a value"
    assert directives["shout"]["locations"] == ["FIELD"]


# --- SDL stays free of introspection ----------------------------------------------------------------


def test_introspection_types_are_excluded_from_sdl() -> None:
    """Names beginning with `__` are reserved (§ Reserved Names) and implicit in every schema, so
    SDL must not declare them -- otherwise the output isn't portable to another server.
    """
    sdl = _schema().to_sdl()

    for reserved in ("__Schema", "__Type", "__Field", "__InputValue", "__EnumValue", "__Directive", "__TypeKind"):
        assert reserved not in sdl
    assert "__schema" not in sdl
    assert "__type" not in sdl


def test_user_types_still_render_in_sdl() -> None:
    sdl = _schema().to_sdl()

    assert "type Item implements Node {" in sdl
    assert "enum Colour {" in sdl
    assert "input ItemFilter {" in sdl


def test_query_type_description_survives_the_introspection_subclass() -> None:
    """The injected `__schema`/`__type` fields live on a *subclass* of the user's query type; that
    subclass has to carry the original's description forward or it silently vanishes from SDL.
    """

    @bramble.type(description="The root query type")
    class DescribedQuery:
        hello: str = "hi"

    assert '"""The root query type"""\ntype DescribedQuery' in bramble.Schema(query=DescribedQuery).to_sdl()


def test_schema_query_attribute_is_still_the_users_own_class() -> None:
    assert _schema().query is Query


# --- Composition with federation ---------------------------------------------------------------------


def test_federation_schema_is_also_introspectable() -> None:
    """`bramble.federation.Schema` synthesizes its own query subclass before the base class adds
    the introspection one -- the two layers have to compose, not clobber each other.
    """
    schema = federation.Schema(query=FederatedQuery, types=[Product])
    result = schema.execute("{ __schema { queryType { name } } }")

    assert result == {"data": {"__schema": {"queryType": {"name": "FederatedQuery"}}}}
    # ...and federation's own synthetic fields still work alongside introspection.
    assert "_service" in schema.execute("{ _service { sdl } }")["data"]


# --- Argument default values ------------------------------------------------------------------------


def test_introspection_reports_argument_default_values_as_graphql_literals() -> None:
    """`__InputValue.defaultValue` is spec'd as a *string* holding the GraphQL literal. It used to
    be hardcoded `None`, so every defaulted argument introspected as a required one -- codegen and
    client-side validation would reject queries the server actually accepts.
    """

    @bramble.type
    class Query:
        @bramble.field
        def search(limit: int = 10, term: str = "all", cursor: str | None = None) -> str:
            return term

    schema = bramble.Schema(query=Query)
    result = schema.execute(
        '{ __type(name: "Query") { fields { name args { name defaultValue } } } }'
    )

    fields = {field["name"]: field for field in result["data"]["__type"]["fields"]}
    args = {arg["name"]: arg["defaultValue"] for arg in fields["search"]["args"]}

    assert args == {"limit": "10", "term": '"all"', "cursor": "null"}


def test_introspection_reports_no_default_value_for_a_required_argument() -> None:
    @bramble.type
    class Query:
        @bramble.field
        def greet(name: str) -> str:
            return name

    schema = bramble.Schema(query=Query)
    result = schema.execute('{ __type(name: "Query") { fields { name args { name defaultValue } } } }')

    fields = {field["name"]: field for field in result["data"]["__type"]["fields"]}
    assert fields["greet"]["args"] == [{"name": "name", "defaultValue": None}]


@bramble.input
class _DefaultsFilter:
    limit: int = 10
    cursor: str | None = None


def test_introspection_reports_input_field_default_values() -> None:
    @bramble.type
    class Query:
        @bramble.field
        def search(filter: _DefaultsFilter) -> int:
            return filter.limit

    schema = bramble.Schema(query=Query, types=[_DefaultsFilter])
    result = schema.execute('{ __type(name: "_DefaultsFilter") { inputFields { name defaultValue } } }')

    defaults = {f["name"]: f["defaultValue"] for f in result["data"]["__type"]["inputFields"]}
    assert defaults == {"limit": "10", "cursor": "null"}


# --- Standard-library scalars -----------------------------------------------------------------------


@bramble.type
class _Reading:
    at: datetime.datetime
    on: datetime.date
    taken: datetime.time
    identifier: uuid.UUID
    amount: decimal.Decimal


@bramble.type
class _ScalarQuery:
    @bramble.field
    def reading() -> _Reading:
        return _Reading(
            at=datetime.datetime(2026, 1, 1),
            on=datetime.date(2026, 1, 1),
            taken=datetime.time(12, 0),
            identifier=uuid.UUID(int=1),
            amount=decimal.Decimal("1.5"),
        )


def _referenced_type_names(node: dict | None) -> set[str]:
    """Every named type a `...TypeRef`-shaped fragment of an introspection result points at,
    unwrapping `ofType` chains.
    """
    names: set[str] = set()
    while node is not None:
        if node.get("name") is not None:
            names.add(node["name"])
        node = node.get("ofType")
    return names


def test_standard_library_scalars_are_reported_as_types() -> None:
    """`datetime`/`date`/`time`/`UUID`/`Decimal` are named and serialized with no registration at
    all, so they never appear in `scalar_map` -- the SDL still declares them, and introspection has
    to agree, or a client rejects the result with "unknown type: DateTime".
    """
    types = _types_by_name(bramble.Schema(query=_ScalarQuery).execute(INTROSPECTION_QUERY))

    for name in ("DateTime", "Date", "Time", "UUID", "Decimal"):
        assert types[name]["kind"] == "SCALAR", name
    assert types["DateTime"]["description"] == "Date with time (isoformat)"


def test_an_unreferenced_standard_library_scalar_is_not_reported() -> None:
    """Same rule the SDL renders by: only the built-ins a schema actually refers to are declared."""

    @bramble.type
    class Query:
        @bramble.field
        def at() -> datetime.datetime:
            return datetime.datetime(2026, 1, 1)

    types = _types_by_name(bramble.Schema(query=Query).execute(INTROSPECTION_QUERY))

    assert "DateTime" in types
    assert "UUID" not in types


def test_every_referenced_type_is_present_in_the_result() -> None:
    """The invariant a client's `buildClientSchema` enforces, and the one a missing scalar breaks:
    every type named anywhere in the result -- as a field's type, an argument's, an interface, a
    union member -- must itself be one of the reported types.
    """
    result = bramble.Schema(query=_ScalarQuery, types=[Item], directives=[shout]).execute(INTROSPECTION_QUERY)
    types = _types_by_name(result)

    referenced: set[str] = set()
    for entry in types.values():
        for field in entry["fields"] or ():
            referenced |= _referenced_type_names(field["type"])
            for argument in field["args"]:
                referenced |= _referenced_type_names(argument["type"])
        for input_field in entry["inputFields"] or ():
            referenced |= _referenced_type_names(input_field["type"])
        for related in (entry["interfaces"] or []) + (entry["possibleTypes"] or []):
            referenced |= _referenced_type_names(related)
    for directive in result["data"]["__schema"]["directives"]:
        for argument in directive["args"]:
            referenced |= _referenced_type_names(argument["type"])

    assert referenced - set(types) == set()
