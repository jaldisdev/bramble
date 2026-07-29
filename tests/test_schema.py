from __future__ import annotations

import base64
from typing import Annotated, NewType, Union

import pytest

import bramble
from bramble.directive import DirectiveLocation, DirectiveValue
from bramble.schema.config import SchemaConfig
from bramble.schema_directive import Location as SchemaDirectiveLocation

# `typing.get_type_hints` can't see an enclosing test function's local scope (only module
# globals), so a NewType/Annotated alias referenced by a field must live at module level here --
# same reason bramble's own root classes (query/mutation/types=[...]) don't have this problem:
# Schema() explicitly seeds `localns` with those, but it has no visibility into bare aliases.
Base64 = NewType("Base64", bytes)


@bramble.type
class _AudioForUnionTest:
    title: str


@bramble.type
class _VideoForUnionTest:
    title: str


MediaItem = Annotated[Union[_AudioForUnionTest, _VideoForUnionTest], bramble.union("MediaItem")]


def test_minimal_schema_succeeds() -> None:
    @bramble.type
    class Query:
        hello: str

    schema = bramble.Schema(query=Query)

    assert schema.query is Query
    assert schema.types_by_name["Query"] is Query


def test_schema_requires_bramble_decorated_query() -> None:
    with pytest.raises(bramble.SchemaError):
        bramble.Schema(query=object)


def test_type_reachable_via_field_is_discovered() -> None:
    @bramble.type
    class Author:
        name: str

    @bramble.type
    class Query:
        author: Author

    schema = bramble.Schema(query=Query, types=[Author])

    assert schema.types_by_name["Author"] is Author


def test_type_only_reachable_via_types_param_is_included() -> None:
    @bramble.interface
    class Shape:
        radius: float

    @bramble.type
    class Circle(Shape):
        color: str

    @bramble.type
    class Query:
        shape: Shape

    schema = bramble.Schema(query=Query, types=[Shape, Circle])

    assert schema.types_by_name["Circle"] is Circle
    assert schema.implementors_by_interface["Shape"] == [Circle]


def test_interface_registered_even_when_not_directly_referenced() -> None:
    @bramble.interface
    class Shape:
        radius: float

    @bramble.type
    class Circle(Shape):
        color: str

    @bramble.type
    class Query:
        circle: Circle

    # Shape is never a field's own annotation anywhere -- only reachable via Circle's MRO.
    schema = bramble.Schema(query=Query, types=[Shape, Circle])

    assert schema.types_by_name["Shape"] is Shape
    assert schema.implementors_by_interface["Shape"] == [Circle]


def test_multiple_implementors_of_same_interface() -> None:
    @bramble.interface
    class Shape:
        radius: float

    @bramble.type
    class Circle(Shape):
        pass

    @bramble.type
    class Square(Shape):
        pass

    @bramble.type
    class Query:
        shape: Shape

    schema = bramble.Schema(query=Query, types=[Shape, Circle, Square])

    assert set(schema.implementors_by_interface["Shape"]) == {Circle, Square}


def test_nullable_covariance_violation_fails_at_construction() -> None:
    @bramble.interface
    class Shape:
        name: str

    @bramble.type
    class Circle(Shape):
        name: str | None
        radius: float

    @bramble.type
    class Query:
        shape: Shape

    with pytest.raises(bramble.SchemaError, match="non-null"):
        bramble.Schema(query=Query, types=[Shape, Circle])


def test_narrowing_nullability_is_allowed() -> None:
    @bramble.interface
    class Shape:
        name: str | None

    @bramble.type
    class Circle(Shape):
        name: str
        radius: float

    @bramble.type
    class Query:
        shape: Shape

    # Should not raise: a non-null implementor field satisfies a nullable interface field.
    schema = bramble.Schema(query=Query, types=[Shape, Circle])
    assert schema.implementors_by_interface["Shape"] == [Circle]


def test_extra_required_argument_on_implementor_field_fails() -> None:
    @bramble.interface
    class Node:
        @bramble.field
        def label(parent: bramble.Parent[Node]) -> str:
            return ""

    @bramble.type
    class Item(Node):
        @bramble.field
        def label(parent: bramble.Parent[Item], loud: bool) -> str:
            return "ITEM" if loud else "item"

    @bramble.type
    class Query:
        item: Item
        node: Node

    with pytest.raises(bramble.SchemaError, match="required argument"):
        bramble.Schema(query=Query, types=[Node, Item])


def test_extra_optional_argument_on_implementor_field_is_allowed() -> None:
    @bramble.interface
    class Node:
        @bramble.field
        def label(parent: bramble.Parent[Node]) -> str:
            return ""

    @bramble.type
    class Item(Node):
        @bramble.field
        def label(parent: bramble.Parent[Item], loud: bool = False) -> str:
            return "ITEM" if loud else "item"

    @bramble.type
    class Query:
        item: Item
        node: Node

    schema = bramble.Schema(query=Query, types=[Node, Item])
    assert schema.implementors_by_interface["Node"] == [Item]


def test_schema_validates_directives_are_real_bramble_directives() -> None:
    @bramble.type
    class Query:
        hello: str

    @bramble.directive(locations=[DirectiveLocation.FIELD])
    def turn_uppercase(value: DirectiveValue[str]) -> str:
        return value.upper()

    schema = bramble.Schema(query=Query, directives=[turn_uppercase])
    assert schema.directives == (turn_uppercase,)

    def not_a_directive(value: str) -> str:
        return value

    with pytest.raises(bramble.SchemaError):
        bramble.Schema(query=Query, directives=[not_a_directive])


def test_schema_config_scalar_map_is_included() -> None:
    @bramble.type
    class Query:
        data: Base64

    scalar_definition = bramble.scalar(
        name="Base64",
        serialize=lambda v: base64.b64encode(v).decode("utf-8"),
        parse_value=base64.b64decode,
    )
    config = SchemaConfig(scalar_map={Base64: scalar_definition})

    schema = bramble.Schema(query=Query, config=config)

    assert schema.scalars_by_python_type[Base64] is scalar_definition


def test_union_reachable_via_field_is_registered() -> None:
    @bramble.type
    class Query:
        media: MediaItem

    schema = bramble.Schema(query=Query, types=[_AudioForUnionTest, _VideoForUnionTest])

    assert "MediaItem" in schema.unions_by_name
    assert schema.types_by_name["_AudioForUnionTest"] is _AudioForUnionTest
    assert schema.types_by_name["_VideoForUnionTest"] is _VideoForUnionTest
    assert schema.union_members_by_name["MediaItem"] == [_AudioForUnionTest, _VideoForUnionTest]
    assert schema.union_markers_by_name["MediaItem"] is MediaItem.__metadata__[0]


BareMediaItem = Union[_AudioForUnionTest, _VideoForUnionTest]


def test_bare_union_reachable_via_field_is_registered() -> None:
    """A `Union[A, B]` field with no `Annotated[..., bramble.union(...)]` wrapper still needs to
    resolve at execution time (§5's `resolve_type`/`isinstance` fallback dispatch) -- it must be
    registered the same as the explicitly-named form, just with an autogenerated name.
    """

    @bramble.type
    class Query:
        media: BareMediaItem

    schema = bramble.Schema(query=Query, types=[_AudioForUnionTest, _VideoForUnionTest])

    assert "_AudioForUnionTest_VideoForUnionTest" in schema.unions_by_name
    assert schema.union_members_by_name["_AudioForUnionTest_VideoForUnionTest"] == [
        _AudioForUnionTest,
        _VideoForUnionTest,
    ]
    assert schema.union_markers_by_name["_AudioForUnionTest_VideoForUnionTest"] is None


def test_optional_field_is_not_registered_as_a_union() -> None:
    @bramble.type
    class Query:
        maybe_audio: _AudioForUnionTest | None

    schema = bramble.Schema(query=Query)

    assert schema.unions_by_name == {}


def test_schema_directives_applied_to_a_type_are_discovered() -> None:
    @bramble.schema_directive(locations=[SchemaDirectiveLocation.OBJECT])
    class Keys:
        fields: str

    @bramble.type(directives=[Keys(fields="id")])
    class User:
        id: str

    @bramble.type
    class Query:
        user: User

    schema = bramble.Schema(query=Query, types=[User])

    assert "keys" in schema.schema_directives_by_name
    assert schema.schema_directives_by_name["keys"].locations == ["OBJECT"]


def test_schema_directives_applied_to_a_field_are_discovered() -> None:
    @bramble.schema_directive(locations=[SchemaDirectiveLocation.FIELD_DEFINITION])
    class Deprecated:
        reason: str

    @bramble.type
    class Query:
        old_field: str = bramble.field(directives=[Deprecated(reason="use newField")], default="x")

    schema = bramble.Schema(query=Query)

    assert "deprecated" in schema.schema_directives_by_name


def test_schema_directives_applied_to_an_interface_only_are_discovered() -> None:
    """The interface is only reachable here through `ConcreteNode` (its implementor) -- the
    query never references `Node` directly as a field's own annotation, which used to mean
    `_discover_type` never ran on `Node` itself, silently dropping its own applied directives.
    """

    @bramble.schema_directive(locations=[SchemaDirectiveLocation.INTERFACE])
    class InterfaceOnly:
        pass

    @bramble.interface(directives=[InterfaceOnly()])
    class Node:
        id: str

    @bramble.type
    class ConcreteNode(Node):
        id: str

    @bramble.type
    class Query:
        node: ConcreteNode

    schema = bramble.Schema(query=Query, types=[ConcreteNode])

    assert "interfaceOnly" in schema.schema_directives_by_name
