from __future__ import annotations

import asyncio

import bramble
import bramble.federation as federation
from bramble.federation.schema import Schema as FederationSchema

# `bramble.federation.Schema` is a real subclass of `bramble.Schema` (not a delegating wrapper) --
# these tests drive it the same way `tests/test_sdl.py`/`tests/test_execution.py` drive the base
# `Schema`: build one, inspect `to_sdl()`, and execute real queries against it.


@bramble.type(directives=[federation.Key(fields="id")])
class Product:
    id: str
    name: str = "widget"

    @classmethod
    def resolve_reference(cls, id: str) -> "Product":
        return cls(id=id, name=f"Product {id}")


@bramble.type(directives=[federation.Key(fields="id sku")])
class Bundle:
    id: str
    sku: str = ""

    @classmethod
    async def resolve_reference(cls, id: str, sku: str) -> "Bundle":
        return cls(id=id, sku=sku)


@bramble.type
class _Query:
    @bramble.field
    def product(id: str) -> Product:
        return Product(id=id)


def test_sdl_includes_link_directive_and_repeatable_key_declaration() -> None:
    schema = FederationSchema(query=_Query, types=[Product])
    sdl = schema.to_sdl()

    assert 'schema @link(url: "https://specs.apollo.dev/federation/v2.6", import: ["@key"]) {' in sdl
    assert "directive @key(fields: FieldSet!, resolvable: Boolean!) repeatable on OBJECT | INTERFACE" in sdl


def test_sdl_declares_entities_field_with_entity_union_even_for_a_single_entity_type() -> None:
    schema = FederationSchema(query=_Query, types=[Product])
    sdl = schema.to_sdl()

    assert "_entities(representations: [_Any!]!): [_Entity]!" in sdl
    assert "union _Entity = Product" in sdl


def test_sdl_declares_entity_union_with_all_key_tagged_types() -> None:
    schema = FederationSchema(query=_Query, types=[Product, Bundle])
    sdl = schema.to_sdl()

    assert "union _Entity = Product | Bundle" in sdl


def test_service_field_returns_this_schemas_own_sdl() -> None:
    schema = FederationSchema(query=_Query, types=[Product])

    result = asyncio.run(schema.execute_async("{ _service { sdl } }"))

    assert "errors" not in result
    assert "@link" in result["data"]["_service"]["sdl"]


def test_entities_resolves_a_heterogeneous_batch_via_resolve_reference() -> None:
    schema = FederationSchema(query=_Query, types=[Product, Bundle])

    result = asyncio.run(
        schema.execute_async(
            "query($reps: [_Any!]!) { _entities(representations: $reps) "
            "{ ... on Product { id name } ... on Bundle { id sku } } }",
            variable_values={
                "reps": [
                    {"__typename": "Product", "id": "1"},
                    {"__typename": "Bundle", "id": "2", "sku": "SKU-2"},
                ]
            },
        )
    )

    assert result == {
        "data": {
            "_entities": [
                {"id": "1", "name": "Product 1"},
                {"id": "2", "sku": "SKU-2"},
            ]
        }
    }


def test_entities_returns_none_for_an_unknown_typename() -> None:
    schema = FederationSchema(query=_Query, types=[Product])

    result = asyncio.run(
        schema.execute_async(
            "query($reps: [_Any!]!) { _entities(representations: $reps) { ... on Product { id } } }",
            variable_values={"reps": [{"__typename": "NotRegistered", "id": "1"}]},
        )
    )

    assert result == {"data": {"_entities": [None]}}


def test_key_on_a_non_bramble_type_raises_a_clear_error() -> None:
    class NotADecoratedType:
        pass

    NotADecoratedType.__bramble_applied_directives__ = (federation.Key(fields="id"),)

    @bramble.type
    class Query:
        greet: str

    try:
        FederationSchema(query=Query, types=[NotADecoratedType])
    except bramble.SchemaError as error:
        assert "not a @bramble.type-decorated class" in str(error)
    else:
        raise AssertionError("expected a SchemaError")


def test_key_referencing_an_unknown_field_raises_a_clear_error() -> None:
    @bramble.type(directives=[federation.Key(fields="bogus")], name="BadKeyProduct")
    class BadKeyProduct:
        id: str

    @bramble.type
    class Query:
        product: BadKeyProduct

    try:
        FederationSchema(query=Query, types=[BadKeyProduct])
    except bramble.SchemaError as error:
        assert "references unknown field 'bogus'" in str(error)
    else:
        raise AssertionError("expected a SchemaError")


def test_key_with_a_nested_selection_set_is_rejected_as_not_yet_supported() -> None:
    @bramble.type(directives=[federation.Key(fields="organization { id }")], name="NestedKeyProduct")
    class NestedKeyProduct:
        id: str

    @bramble.type
    class Query:
        product: NestedKeyProduct

    try:
        FederationSchema(query=Query, types=[NestedKeyProduct])
    except bramble.SchemaError as error:
        assert "not yet supported" in str(error)
    else:
        raise AssertionError("expected a SchemaError")


def test_no_entity_types_omits_the_entities_field_but_keeps_service() -> None:
    @bramble.type
    class PlainQuery:
        greet: str

    schema = FederationSchema(query=PlainQuery)
    sdl = schema.to_sdl()

    assert "_service: _Service!" in sdl
    assert "_entities" not in sdl
