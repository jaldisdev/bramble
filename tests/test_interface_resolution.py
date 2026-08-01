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

import pytest

import bramble
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
