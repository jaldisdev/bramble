"""`_Any`/`FieldSet` -- the two scalars the Apollo Federation v2 spec itself requires, both trivial
identity-passthrough scalars matching `bramble.Upload`'s own established pattern
(`bramble/_scalar.py`): no runtime coercion, whatever value is already there passes straight
through.
"""

from __future__ import annotations

from typing import NewType

from bramble._scalar import scalar

_Any = NewType("_Any", dict)
"""An entity "representation" -- a JSON object with at least a `__typename` key plus that type's
own `@key` fields, e.g. `{"__typename": "Product", "id": "123"}`. This is exactly what the
`_entities(representations: [_Any!]!)` field's argument holds one of per list entry.
"""

AnyDefinition = scalar(
    name="_Any",
    serialize=lambda value: value,
    parse_value=lambda value: value,
)

FieldSet = NewType("FieldSet", str)
"""A GraphQL selection-set string, e.g. `"id"` or `"id sku"` -- the spec's own type for
`@key`/`@requires`/`@provides`'s `fields` argument. Registered as its own scalar purely so those
arguments render as `FieldSet!` in SDL rather than `String!` -- bramble validates `@key`'s value at
`federation.Schema` build time (see `bramble/federation/schema.py`), not via this scalar's
`parse_value` (identity, like `_Any`).
"""

FieldSetDefinition = scalar(
    name="FieldSet",
    serialize=lambda value: value,
    parse_value=lambda value: value,
)

__all__ = ["AnyDefinition", "FieldSetDefinition", "FieldSet", "_Any"]
