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

from types import SimpleNamespace

import pytest

import bramble
from bramble._error import GraphQLError
from bramble._interface import resolve_interface_type


class DomainCircle:
    def is_circle(self) -> bool:
        return True


class DomainSquare:
    def is_circle(self) -> bool:
        return False


@bramble.interface
class Shape:
    pass


@bramble.type
class Circle(Shape):
    @classmethod
    def is_type_of(cls, obj: object, info: object) -> bool:
        return obj.is_circle()


@bramble.type
class Square(Shape):
    @classmethod
    def is_type_of(cls, obj: object, info: object) -> bool:
        return not obj.is_circle()


def test_is_type_of_resolves_correctly() -> None:
    assert resolve_interface_type([Circle, Square], DomainCircle(), info=None) is Circle
    assert resolve_interface_type([Circle, Square], DomainSquare(), info=None) is Square


def test_isinstance_fallback_when_no_is_type_of_defined() -> None:
    @bramble.type
    class Triangle(Shape):
        base: float

    @bramble.type
    class Hexagon(Shape):
        side: float

    triangle = Triangle(base=1.0)
    hexagon = Hexagon(side=2.0)

    assert resolve_interface_type([Triangle, Hexagon], triangle, info=None) is Triangle
    assert resolve_interface_type([Triangle, Hexagon], hexagon, info=None) is Hexagon


def test_no_matching_type_raises_graphql_error() -> None:
    @bramble.type
    class Triangle(Shape):
        base: float

    @bramble.type
    class Hexagon(Shape):
        side: float

    class Unrelated:
        pass

    with pytest.raises(bramble.GraphQLError) as excinfo:
        resolve_interface_type([Triangle, Hexagon], Unrelated(), info=None)

    assert excinfo.value.code is bramble.ErrorCode.INTERFACE_TYPE_RESOLUTION_FAILED


def test_ambiguous_match_raises_graphql_error() -> None:
    @bramble.type
    class AlwaysA(Shape):
        @classmethod
        def is_type_of(cls, obj: object, info: object) -> bool:
            return True

    @bramble.type
    class AlwaysB(Shape):
        @classmethod
        def is_type_of(cls, obj: object, info: object) -> bool:
            return True

    with pytest.raises(bramble.GraphQLError) as excinfo:
        resolve_interface_type([AlwaysA, AlwaysB], object(), info=None)

    assert excinfo.value.code is bramble.ErrorCode.INTERFACE_TYPE_RESOLUTION_FAILED


def test_graphql_error_is_a_real_exception_with_spec_shaped_fields() -> None:
    error = bramble.GraphQLError(
        "boom",
        code=bramble.ErrorCode.INTERFACE_TYPE_RESOLUTION_FAILED,
    )

    assert isinstance(error, Exception)
    assert str(error) == "boom"
    assert error.message == "boom"
    assert error.code is bramble.ErrorCode.INTERFACE_TYPE_RESOLUTION_FAILED
    assert error.locations is None
    assert error.path is None
    assert error.extensions == {}


@bramble.interface
class _CastNode:
    name: str


@bramble.type
class _CastDog(_CastNode):
    pass


class _CastRow:
    """Deliberately not an instance of any implementor."""


def test_cast_tags_a_value_that_isinstance_cannot_identify() -> None:
    """The escape hatch for an interface value that isn't an instance of any implementor -- a dict
    or ORM row standing in for a GraphQL type, where neither `is_type_of` nor `isinstance` works.
    """

    @bramble.type
    class Query:
        @bramble.field
        def node() -> _CastNode:
            row = _CastRow()
            row.name = "from a row"
            return bramble.cast(_CastDog, row)

    schema = bramble.Schema(query=Query, types=[_CastDog])
    result = schema.execute("{ node { __typename name } }")

    assert result["data"] == {"node": {"__typename": "_CastDog", "name": "from a row"}}


def test_cast_returns_values_it_cannot_tag_unchanged() -> None:
    # An `int`/`str`/tuple has nowhere to put the tag; falling back to normal dispatch beats
    # failing a resolver over it.
    assert bramble.cast(int, 5) == 5
    assert bramble.cast(str, "x") == "x"


# --- `is_type_of` answering with a type ------------------------------------------------------
#
# An interface, unlike a union, has a shared base to hang a single hook on, so declaring
# `is_type_of` once there and returning the concrete class expresses one decision in one place.
# Read as a boolean that cannot work: every implementor inherits the same method, each returns a
# truthy class, and every candidate matches.


@bramble.interface
class _Shape:
    id: str

    def is_type_of(instance, *args, **kwargs):
        return _Square if getattr(instance, "side", None) else _Circle


@bramble.type
class _Square(_Shape):
    side: int


@bramble.type
class _Circle(_Shape):
    radius: int


def _shape_schema(value: object) -> bramble.Schema:
    @bramble.type
    class Query:
        @bramble.field
        def shape() -> _Shape:
            return value

    return bramble.Schema(query=Query, types=[_Square, _Circle])


def test_one_hook_on_the_interface_can_name_the_concrete_type() -> None:
    schema = _shape_schema(SimpleNamespace(id="1", side=3))

    result = schema.execute("{ shape { __typename id ... on _Square { side } } }")

    assert result.get("errors") is None
    assert result["data"] == {"shape": {"__typename": "_Square", "id": "1", "side": 3}}


def test_the_same_hook_selects_the_other_implementor() -> None:
    schema = _shape_schema(SimpleNamespace(id="2", radius=7))

    result = schema.execute("{ shape { __typename ... on _Circle { radius } } }")

    assert result.get("errors") is None
    assert result["data"] == {"shape": {"__typename": "_Circle", "radius": 7}}


@bramble.interface
class _Undecidable:
    id: str

    def is_type_of(instance, *args, **kwargs):
        return None


@bramble.type
class _OnlyOne(_Undecidable):
    id: str


def test_a_type_naming_hook_still_fails_when_it_names_nothing() -> None:
    """Returning `None` means "no match" exactly as `False` does -- ambiguity is never guessed at."""

    @bramble.type
    class Query:
        @bramble.field
        def thing() -> _Undecidable:
            return SimpleNamespace(id="1")

    schema = bramble.Schema(query=Query, types=[_OnlyOne])

    with pytest.raises(GraphQLError, match="no implementing type matched"):
        schema.execute("{ thing { id } }")


@bramble.interface
class _Vehicle:
    id: str


@bramble.type
class _Car(_Vehicle):
    wheels: int

    def is_type_of(instance, *args, **kwargs):
        return getattr(instance, "wheels", 0) == 4


@bramble.type
class _Bike(_Vehicle):
    wheels: int

    def is_type_of(instance, *args, **kwargs):
        return getattr(instance, "wheels", 0) == 2


def test_per_type_boolean_hooks_are_unaffected() -> None:
    """The pre-existing form: each implementor answers "is it me?" for itself."""

    @bramble.type
    class Query:
        @bramble.field
        def vehicle() -> _Vehicle:
            return SimpleNamespace(id="1", wheels=2)

    schema = bramble.Schema(query=Query, types=[_Car, _Bike])
    result = schema.execute("{ vehicle { __typename } }")

    assert result.get("errors") is None
    assert result["data"] == {"vehicle": {"__typename": "_Bike"}}
