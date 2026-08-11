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
import enum as enum_module
from collections.abc import Callable, Sequence
from typing import Any, TypeVar

from bramble._bramble import SchemaError, process_enum
from bramble.schema_directive import Location

_enum_type = enum_module.Enum
EnumType = TypeVar("EnumType", bound=type[_enum_type])


@dataclasses.dataclass(frozen=True)
class EnumValueDefinition:
    """What `bramble.enum_value(...)` produces. Assigned *as a member's value* inside an enum body,
    where it stands in for the real value until `@bramble.enum` unwraps it back onto the member --
    so `RED = bramble.enum_value("red", description="...")` still leaves `Color.RED.value == "red"`
    for ordinary Python code.
    """

    value: Any
    graphql_name: str | None = None
    description: str | None = None
    deprecation_reason: str | None = None
    directives: tuple[object, ...] = ()


def enum_value(
    value: Any,
    *,
    name: str | None = None,
    description: str | None = None,
    deprecation_reason: str | None = None,
    directives: Sequence[object] = (),
) -> Any:
    """Customises one enum member -- its GraphQL name, description, deprecation, or applied
    directives -- by standing in for that member's value:

        @bramble.enum
        class Color:
            RED = bramble.enum_value("red", description="The colour red")
            GREEN = "green"

    The member's actual Python value (`"red"` above) is restored onto the member by
    `@bramble.enum`, so `Color.RED.value` is unaffected. Returns `Any` rather than
    `EnumValueDefinition` so a type checker doesn't complain about the declared member type.
    """
    return EnumValueDefinition(
        value=value,
        graphql_name=name,
        description=description,
        deprecation_reason=deprecation_reason,
        directives=tuple(directives),
    )


def _restore_member_values(cls: type[_enum_type]) -> None:
    """Puts each member's real value back after `enum_value(...)` stood in for it.

    `enum.Enum` has already frozen `_value2member_map_` around the placeholder object by the time
    this runs (the metaclass builds it while executing the class body), so both that map and the
    member's own `_value_` need rewriting -- otherwise `Color("red")` and `Color.RED.value` would
    both still see the `EnumValueDefinition` rather than `"red"`.
    """
    for member in cls:
        placeholder = member.value
        if not isinstance(placeholder, EnumValueDefinition):
            continue
        cls._value2member_map_.pop(placeholder, None)
        member._value_ = placeholder.value
        cls._value2member_map_[placeholder.value] = member


def _process_enum(
    cls: type[_enum_type],
    *,
    name: str | None,
    description: str | None,
    directives: Sequence[object],
) -> type[_enum_type]:
    from bramble._type import _validate_directive_locations

    cls_name = getattr(cls, "__name__", str(cls))
    # Checked here rather than relying on `process_enum`'s own identical check further down: the
    # member scan below iterates `cls`, which raises an opaque `TypeError: 'type' object is not
    # iterable` on a non-enum long before Rust ever gets a chance to report it properly.
    if not isinstance(cls, enum_module.EnumMeta):
        raise SchemaError(
            f"'{cls_name}' is not an enum -- @bramble.enum can only decorate an enum.Enum subclass"
        )

    _validate_directive_locations(directives, Location.ENUM, cls_name)

    # Each member's own `enum_value(directives=[...])` is checked against ENUM_VALUE, the same way
    # a field's directives are checked against FIELD_DEFINITION -- read before `_restore_member_values`
    # below puts the real values back and the markers are no longer reachable from the members.
    value_directives: dict[str, tuple[object, ...]] = {}
    for member in cls:
        marker = member.value
        if isinstance(marker, EnumValueDefinition) and marker.directives:
            _validate_directive_locations(marker.directives, Location.ENUM_VALUE, f"{cls_name}.{member.name}")
            value_directives[member.name] = marker.directives

    # `process_enum` (Rust) reads each member's `enum_value(...)` marker off its `.value`, so it
    # must run *before* `_restore_member_values` swaps those markers back out for the real values.
    cls.__bramble_type_info__ = process_enum(
        cls,
        name=name,
        description=description,
        directives=tuple(directives),
    )
    _restore_member_values(cls)
    # Mirrors `bramble._type._process_type`: the graph walker (`bramble._schema`) needs the
    # directive *classes* actually in use to render their own `directive @name(...) on ...`
    # declarations, which the already-extracted values alone can't reconstruct. Member-level
    # directives need their own map for the same reason -- unlike a dataclass field, an enum
    # member has nowhere of its own to hang them once its value is restored.
    cls.__bramble_applied_directives__ = tuple(directives)
    cls.__bramble_enum_value_directives__ = value_directives
    return cls


def enum(
    cls: EnumType | None = None,
    *,
    name: str | None = None,
    description: str | None = None,
    directives: Sequence[object] = (),
) -> Any:
    """Declares a Python `enum.Enum` subclass as a GraphQL enum type:

        @bramble.enum
        class Color(enum.Enum):
            RED = "red"
            GREEN = "green"

    Renders as `enum Color { RED GREEN }` -- a member's GraphQL name is its Python *identifier*,
    not its value, matching how a GraphQL enum travels by name over the wire. The value stays a
    private Python detail resolvers can use however they like. Use `bramble.enum_value(...)` to
    override a member's GraphQL name or attach a description/deprecation/directives.
    """

    def wrap(inner: EnumType) -> EnumType:
        return _process_enum(inner, name=name, description=description, directives=directives)

    if cls is None:
        return wrap
    return wrap(cls)


def is_enum_type(candidate: Any) -> bool:
    """Whether `candidate` is a `@bramble.enum`-decorated class -- the check execution and the
    schema graph walker both use to tell an enum apart from an object/interface/input type, all of
    which carry `__bramble_type_info__`.
    """
    info = getattr(candidate, "__bramble_type_info__", None)
    return info is not None and info.kind == "enum"


__all__ = ["EnumValueDefinition", "enum", "enum_value", "is_enum_type"]
