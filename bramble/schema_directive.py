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

import dataclasses
import enum
import sys
from collections.abc import Sequence
from typing import Any

from bramble._bramble import describe_schema_directive

_type = type  # capture the builtin before it's shadowed by a `type` parameter's annotation


class Location(enum.Enum):
    SCHEMA = "SCHEMA"
    SCALAR = "SCALAR"
    OBJECT = "OBJECT"
    FIELD_DEFINITION = "FIELD_DEFINITION"
    ARGUMENT_DEFINITION = "ARGUMENT_DEFINITION"
    INTERFACE = "INTERFACE"
    UNION = "UNION"
    ENUM = "ENUM"
    ENUM_VALUE = "ENUM_VALUE"
    INPUT_OBJECT = "INPUT_OBJECT"
    INPUT_FIELD_DEFINITION = "INPUT_FIELD_DEFINITION"


class DirectiveField(dataclasses.Field):
    """What `bramble.directive_field(...)` produces -- a schema-directive field with an explicit
    GraphQL name, for when the Python identifier can't be spelled the same way (a reserved word,
    say, or a name that must not be camelCased).
    """

    def __init__(self, name: str, *, default: Any = dataclasses.MISSING) -> None:
        kwargs: dict[str, Any] = {"kw_only": True}
        if sys.version_info >= (3, 14):
            kwargs["doc"] = None

        super().__init__(
            default=default,
            default_factory=dataclasses.MISSING,
            init=True,
            repr=True,
            compare=True,
            hash=None,
            metadata=None,
            **kwargs,
        )
        self.graphql_name = name


def directive_field(name: str, *, default: Any = dataclasses.MISSING) -> Any:
    """Overrides the GraphQL name of one `@bramble.schema_directive` field.

        @bramble.schema_directive(locations=[Location.OBJECT])
        class Key:
            fields: str
            resolvable: bool = bramble.directive_field("resolvable", default=True)
    """
    return DirectiveField(name, default=default)


def schema_directive(
    locations: Sequence[Location],
    *,
    name: str | None = None,
    description: str | None = None,
    repeatable: bool = False,
) -> Any:
    """Declares a *schema* directive -- declarative metadata applied to a type, field, argument,
    enum, scalar, or the schema block itself, and rendered into SDL.

        @bramble.schema_directive(locations=[Location.OBJECT, Location.INTERFACE])
        class Key:
            fields: str

        @bramble.type(directives=[Key(fields="id")])
        class User:
            id: bramble.ID

    Renders `type User @key(fields: "id")`, plus the matching `directive @key(...) on OBJECT`
    declaration. Purely declarative: schema directives have no execution behaviour, which is what
    distinguishes them from `bramble.directive` (operation directives). Applying one at a location
    it doesn't declare is a build-time error.

    Arguments:
        locations: the SDL locations this directive may be applied to.
        name: the GraphQL directive name, overriding the camelCased class name.
        description: rendered as the directive's SDL description.
        repeatable: renders the `repeatable` keyword, allowing multiple applications at one site.
    """

    def wrap(cls: _type) -> _type:
        cls = dataclasses.dataclass(cls, kw_only=True)
        cls.__bramble_directive_info__ = describe_schema_directive(
            cls,
            locations=[location.value for location in locations],
            name=name,
            description=description,
            repeatable=repeatable,
        )
        return cls

    return wrap
