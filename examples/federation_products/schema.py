"""A small Apollo Federation v2 subgraph: a `Product` entity keyed by `id`, resolvable from a
gateway via `resolve_reference`. See `bramble/federation/` for the machinery this builds on --
`bramble.federation.type`/`Schema` are the only federation-specific imports a subgraph actually
needs; `_service`/`_entities`/`_Any`/`FieldSet`/`@link` are all synthesized automatically.

This file is meant to double as documentation, mirroring `examples/blog/schema.py`'s own role: no
test assertions of its own (see `tests/test_examples_federation_products.py` for those).
"""

from __future__ import annotations

import dataclasses

import bramble
import bramble.federation as federation


@dataclasses.dataclass
class ProductRecord:
    id: str
    name: str
    price: float


class Catalog:
    """Stands in for a real datastore -- passed as `Schema.execute_async(..., context=catalog)`,
    reachable from any resolver via `Info.context`.
    """

    def __init__(self) -> None:
        self.products: dict[str, ProductRecord] = {
            "p1": ProductRecord(id="p1", name="Keyboard", price=49.99),
            "p2": ProductRecord(id="p2", name="Mouse", price=19.99),
        }


@federation.type(keys=["id"])
class Product:
    id: str
    name: str
    price: float

    @classmethod
    async def resolve_reference(cls, id: str, info: bramble.Info) -> "Product | None":
        """Called once per representation in an incoming `_entities` request -- the gateway
        supplies only the `@key` fields (`id` here), so a real implementation looks the rest up
        from a database/service rather than trusting any other fields on the representation.
        Declaring an `info` parameter (by name) gets the same `Info` a normal field resolver
        would, e.g. for `info.context` database access, exactly like `Query.product` below.
        """
        catalog: Catalog = info.context
        record = catalog.products.get(id)
        if record is None:
            return None
        return cls(id=record.id, name=record.name, price=record.price)


@bramble.type
class Query:
    @bramble.field
    def product(id: str, info: bramble.Info) -> Product | None:
        catalog: Catalog = info.context
        record = catalog.products.get(id)
        if record is None:
            return None
        return Product(id=record.id, name=record.name, price=record.price)


def build_schema() -> federation.Schema:
    return federation.Schema(query=Query, types=[Product])
