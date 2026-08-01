"""Exercises the `examples/federation_products` subgraph end to end -- see
`examples/federation_products/schema.py` for the schema itself.
"""

from __future__ import annotations

import asyncio

import pytest

import bramble.federation as federation
from examples.federation_products.schema import Catalog, build_schema


@pytest.fixture
def schema() -> federation.Schema:
    return build_schema()


@pytest.fixture
def catalog() -> Catalog:
    return Catalog()


def test_service_sdl_is_a_spec_shaped_federation_v2_subgraph(schema: federation.Schema) -> None:
    result = asyncio.run(schema.execute_async("{ _service { sdl } }"))

    sdl = result["data"]["_service"]["sdl"]
    assert 'schema @link(url: "https://specs.apollo.dev/federation/v2.6", import: ["@key"]) {' in sdl
    assert 'type Product @key(fields: "id", resolvable: true) {' in sdl
    assert "_entities(representations: [_Any!]!): [_Entity]!" in sdl
    assert "union _Entity = Product" in sdl


def test_query_product_resolves_via_context(schema: federation.Schema, catalog: Catalog) -> None:
    result = asyncio.run(
        schema.execute_async('{ product(id: "p1") { id name price } }', context=catalog)
    )

    assert result == {"data": {"product": {"id": "p1", "name": "Keyboard", "price": 49.99}}}


def test_entities_resolves_a_known_product_via_resolve_reference(
    schema: federation.Schema, catalog: Catalog
) -> None:
    result = asyncio.run(
        schema.execute_async(
            "query($reps: [_Any!]!) { _entities(representations: $reps) { ... on Product { id name price } } }",
            variable_values={"reps": [{"__typename": "Product", "id": "p2"}]},
            context=catalog,
        )
    )

    assert result == {"data": {"_entities": [{"id": "p2", "name": "Mouse", "price": 19.99}]}}


def test_entities_returns_none_for_an_unknown_product_id(schema: federation.Schema, catalog: Catalog) -> None:
    result = asyncio.run(
        schema.execute_async(
            "query($reps: [_Any!]!) { _entities(representations: $reps) { ... on Product { id } } }",
            variable_values={"reps": [{"__typename": "Product", "id": "does-not-exist"}]},
            context=catalog,
        )
    )

    assert result == {"data": {"_entities": [None]}}
