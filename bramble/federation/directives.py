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

"""The Apollo Federation v2 directive set, each a `@bramble.schema_directive` -- locations and
`repeatable` flags match the spec's own directive declarations exactly (`@link
https://specs.apollo.dev/federation/v2.6`), not an approximation. `@extends` (a v1-era alternative
to `extend type` syntax) is deliberately not included: bramble's SDL renderer has no `extend type`
concept to pair it with, so it would have no meaningful effect here.
"""

from __future__ import annotations

from bramble.federation.scalars import FieldSet
from bramble.schema_directive import Location, directive_field, schema_directive

_ABSTRACT_LOCATIONS = (
    Location.FIELD_DEFINITION,
    Location.OBJECT,
    Location.INTERFACE,
    Location.SCALAR,
    Location.ENUM,
)


@schema_directive(locations=[Location.OBJECT, Location.INTERFACE], repeatable=True)
class Key:
    fields: FieldSet
    resolvable: bool = True


@schema_directive(locations=[Location.OBJECT, Location.FIELD_DEFINITION], repeatable=True)
class Shareable:
    pass


@schema_directive(locations=[Location.OBJECT, Location.FIELD_DEFINITION])
class External:
    pass


@schema_directive(locations=[Location.FIELD_DEFINITION])
class Requires:
    fields: FieldSet


@schema_directive(locations=[Location.FIELD_DEFINITION])
class Provides:
    fields: FieldSet


@schema_directive(locations=[Location.FIELD_DEFINITION])
class Override:
    from_: str = directive_field(name="from")
    label: str | None = directive_field(name="label", default=None)


@schema_directive(
    locations=[
        Location.FIELD_DEFINITION,
        Location.OBJECT,
        Location.INTERFACE,
        Location.UNION,
        Location.ARGUMENT_DEFINITION,
        Location.SCALAR,
        Location.ENUM,
        Location.ENUM_VALUE,
        Location.INPUT_OBJECT,
        Location.INPUT_FIELD_DEFINITION,
    ]
)
class Inaccessible:
    pass


@schema_directive(
    locations=[
        Location.FIELD_DEFINITION,
        Location.OBJECT,
        Location.INTERFACE,
        Location.UNION,
        Location.ARGUMENT_DEFINITION,
        Location.SCALAR,
        Location.ENUM,
        Location.ENUM_VALUE,
        Location.INPUT_OBJECT,
        Location.INPUT_FIELD_DEFINITION,
        Location.SCHEMA,
    ],
    repeatable=True,
)
class Tag:
    name: str


@schema_directive(locations=[Location.OBJECT])
class InterfaceObject:
    pass


@schema_directive(locations=[Location.SCHEMA], repeatable=True)
class ComposeDirective:
    name: str


@schema_directive(locations=list(_ABSTRACT_LOCATIONS))
class Authenticated:
    pass


@schema_directive(locations=list(_ABSTRACT_LOCATIONS))
class RequiresScopes:
    scopes: list[list[str]]


@schema_directive(locations=list(_ABSTRACT_LOCATIONS))
class Policy:
    policies: list[list[str]]


@schema_directive(locations=[Location.SCHEMA], repeatable=True)
class Link:
    url: str
    import_: list[str] | None = directive_field(name="import", default=None)


__all__ = [
    "Authenticated",
    "ComposeDirective",
    "External",
    "Inaccessible",
    "InterfaceObject",
    "Key",
    "Link",
    "Override",
    "Policy",
    "Provides",
    "Requires",
    "RequiresScopes",
    "Shareable",
    "Tag",
]
