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
from collections.abc import AsyncGenerator

import pytest

import bramble


def test_nested_types() -> None:
    @bramble.type
    class Author:
        name: str

    @bramble.type
    class Post:
        title: str
        author: Author

    info = Post.__bramble_type_info__
    assert [f.name for f in info.fields] == ["title", "author"]
    author_field = next(f for f in info.fields if f.name == "author")
    assert author_field.graphql_type == "Author!"


def test_field_name_and_description_overrides_reach_the_schema_ir() -> None:
    @bramble.type
    class Query:
        internal: str = bramble.field(name="publicName", description="a public field", default="x")

    field_info = Query.__bramble_type_info__.fields[0]
    assert field_info.graphql_name == "publicName"
    assert field_info.description == "a public field"


def test_field_without_overrides_has_no_graphql_name_or_description() -> None:
    @bramble.type
    class Query:
        plain: str

    field_info = Query.__bramble_type_info__.fields[0]
    assert field_info.graphql_name is None
    assert field_info.description is None


def test_interface_inheritance_chain() -> None:
    @bramble.interface
    class Error:
        message: str

    @bramble.interface
    class FieldError(Error):
        field: str

    @bramble.type
    class PasswordTooShort(FieldError):
        min_length: int

    info = PasswordTooShort.__bramble_type_info__
    assert info.kind == "type"
    assert {f.name for f in info.fields} == {"message", "field", "min_length"}


def test_input_with_one_of() -> None:
    @bramble.input(one_of=True)
    class Filter:
        by_id: int | None = None
        by_name: str | None = None

    info = Filter.__bramble_type_info__
    assert info.kind == "input"
    assert info.one_of is True
    assert {f.name for f in info.fields} == {"by_id", "by_name"}


def test_input_without_one_of_defaults_false() -> None:
    @bramble.input
    class Simple:
        value: int

    assert Simple.__bramble_type_info__.one_of is False


def test_input_with_resolver_field_fails_to_build() -> None:
    with pytest.raises(bramble.SchemaError):

        @bramble.input
        class BadInput:
            @bramble.field
            def computed() -> int:
                return 1


def test_mutation_behaves_like_field() -> None:
    @bramble.type
    class Query:
        @bramble.field
        def hello() -> str:
            return "hi"

    @bramble.type
    class Mutation:
        @bramble.mutation
        def create_hello() -> str:
            return "hi"

    query_field = Query.__bramble_type_info__.fields[0]
    mutation_field = Mutation.__bramble_type_info__.fields[0]

    assert query_field.graphql_type == mutation_field.graphql_type == "String!"
    assert query_field.has_resolver and mutation_field.has_resolver


def test_type_interface_input_share_process_type_code_path() -> None:
    @bramble.type(name="Named")
    class A:
        value: int

    @bramble.interface(name="Named")
    class B:
        value: int

    @bramble.input(name="Named")
    class C:
        value: int

    kinds = {A.__bramble_type_info__.kind, B.__bramble_type_info__.kind, C.__bramble_type_info__.kind}
    assert kinds == {"type", "interface", "input"}
    for cls in (A, B, C):
        info = cls.__bramble_type_info__
        assert info.name == "Named"
        assert [f.name for f in info.fields] == ["value"]


def test_field_supports_both_method_and_plain_function_resolver_syntax() -> None:
    def resolve_greeting() -> str:
        return "hi"

    @bramble.type
    class Query:
        greeting: str = bramble.field(resolver=resolve_greeting)

        @bramble.field
        def farewell() -> str:
            return "bye"

    fields_by_name = {f.name: f for f in Query.__bramble_type_info__.fields}
    assert fields_by_name["greeting"].has_resolver is True
    assert fields_by_name["farewell"].has_resolver is True


def test_bare_and_parameterized_type_decorator() -> None:
    @bramble.type
    class Bare:
        value: int

    @bramble.type(name="Custom", description="a custom type")
    class Parameterized:
        value: int

    assert Bare.__bramble_type_info__.name == "Bare"
    assert Parameterized.__bramble_type_info__.name == "Custom"
    assert Parameterized.__bramble_type_info__.description == "a custom type"


def test_decorated_types_are_real_dataclasses() -> None:
    @bramble.interface
    class Shape:
        radius: float

    @bramble.type
    class Circle(Shape):
        color: str

        @bramble.field
        def area(parent: bramble.Parent[Circle]) -> float:
            return 3.14159 * parent.radius**2

    assert dataclasses.is_dataclass(Shape)
    assert dataclasses.is_dataclass(Circle)

    circle = Circle(radius=2.0, color="red")
    assert circle.radius == 2.0
    assert circle.color == "red"
    assert circle.area() == pytest.approx(12.566, abs=1e-3)

    # kw_only=True: no positional construction, even across inheritance
    with pytest.raises(TypeError):
        Circle(2.0, "red")  # type: ignore[call-arg]


def test_input_types_are_constructible() -> None:
    @bramble.input(one_of=True)
    class Filter:
        by_id: int | None = None
        by_name: str | None = None

    filter_by_id = Filter(by_id=5)
    assert filter_by_id.by_id == 5
    assert filter_by_id.by_name is None


def test_resolver_fields_excluded_from_repr_and_eq() -> None:
    @bramble.type
    class Circle:
        radius: float

        @bramble.field
        def area(parent: bramble.Parent[Circle]) -> float:
            return 3.14159 * parent.radius**2

    first = Circle(radius=2.0)
    second = Circle(radius=2.0)

    assert first == second
    assert "area" not in repr(first)
    assert "radius=2.0" in repr(first)


def test_resolver_restored_as_callable_after_dataclass_processing() -> None:
    @bramble.type
    class Circle:
        radius: float

        @bramble.field
        def area(parent: bramble.Parent[Circle]) -> float:
            return 3.14159 * parent.radius**2

    # dataclasses.dataclass() strips a resolver-backed field's raw class attribute
    # (it has no usable default); bramble must restore it so the method still works.
    assert callable(Circle.area)
    assert Circle(radius=1.0).area() == pytest.approx(3.14159, abs=1e-5)


def test_method_style_field_without_return_annotation_fails_to_build() -> None:
    with pytest.raises(bramble.SchemaError):

        @bramble.type
        class Broken:
            @bramble.field
            def broken(self):
                return 1


def test_field_default_and_default_factory() -> None:
    @bramble.type
    class Config:
        timeout: int = bramble.field(description="request timeout", default=30)
        tags: list = bramble.field(default_factory=list)

    default_config = Config()
    assert default_config.timeout == 30
    assert default_config.tags == []

    overridden = Config(timeout=60, tags=["a"])
    assert overridden.timeout == 60
    assert overridden.tags == ["a"]


def test_field_default_and_default_factory_are_mutually_exclusive() -> None:
    with pytest.raises(bramble.SchemaError):
        bramble.field(default=1, default_factory=list)


def test_field_resolver_and_default_are_mutually_exclusive() -> None:
    with pytest.raises(bramble.SchemaError):
        bramble.field(resolver=lambda: 1, default=5)

    with pytest.raises(bramble.SchemaError):
        # a default set up front, then a resolver attached afterward via `@field(...)`
        # on a method -- the conflict must still be caught when applied out of order.
        deferred = bramble.field(default=5)
        deferred(lambda self: 1)


# Object/instantiation edge cases: decorators applied to non-classes, lambda/staticmethod
# resolvers, and dataclass-derived behavior (asdict/repr) on a bramble type instance.


def test_type_decorator_on_a_non_class_raises() -> None:
    with pytest.raises((TypeError, AttributeError, bramble.SchemaError)):
        bramble.type(lambda: None)


def test_interface_decorator_on_a_non_class_raises() -> None:
    with pytest.raises((TypeError, AttributeError, bramble.SchemaError)):
        bramble.interface(lambda: None)


def test_input_decorator_on_a_non_class_raises() -> None:
    with pytest.raises((TypeError, AttributeError, bramble.SchemaError)):
        bramble.input(lambda: None)


def test_lambda_resolver_with_explicit_field_annotation() -> None:
    @bramble.type
    class Query:
        greeting: str = bramble.field(resolver=lambda: "hi", name="greet")

    assert Query.greeting() == "hi"
    assert Query.__bramble_type_info__.fields[0].graphql_name == "greet"


def test_staticmethod_resolver() -> None:
    class Helpers:
        @staticmethod
        def get_name() -> str:
            return "static-name"

    @bramble.type
    class Query:
        name: str = bramble.field(resolver=Helpers.get_name)

    assert Query.name() == "static-name"


def test_asdict_on_a_simple_type() -> None:
    @bramble.type
    class Point:
        x: int
        y: int

    point = Point(x=1, y=2)
    assert dataclasses.asdict(point) == {"x": 1, "y": 2}


def test_asdict_recurses_into_nested_types() -> None:
    @bramble.type
    class Author:
        name: str

    @bramble.type
    class Post:
        title: str
        author: Author

    post = Post(title="Hello", author=Author(name="Ada"))
    assert dataclasses.asdict(post) == {"title": "Hello", "author": {"name": "Ada"}}


def test_repr_of_a_type_instance_shows_field_values() -> None:
    @bramble.type
    class Point:
        x: int
        y: int

    point = Point(x=1, y=2)
    representation = repr(point)
    assert "Point" in representation
    assert "x=1" in representation
    assert "y=2" in representation


def test_field_extensions_are_rejected_rather_than_silently_ignored() -> None:
    """`extensions=` has no execution hook point yet. Accepting a value and dropping it produced a
    schema that built and ran while doing none of what the extension was for -- the worst outcome.
    """

    class _Extension:
        pass

    with pytest.raises(bramble.SchemaError, match="not implemented yet"):
        bramble.field(extensions=[_Extension()])


def test_field_without_extensions_is_unaffected() -> None:
    @bramble.type
    class Query:
        greeting: str = bramble.field(default="hi", extensions=[])

    assert bramble.Schema(query=Query).execute("{ greeting }", root_value=Query()) == {
        "data": {"greeting": "hi"}
    }


def test_field_deprecation_reason_renders_and_introspects() -> None:
    """Field-level deprecation was the one gap in finding 11 with real user-visible impact:
    `@deprecated` is core GraphQL, and `__Field.isDeprecated` was hardcoded `False`.
    """

    @bramble.type
    class Query:
        @bramble.field(deprecation_reason="use newField")
        def old_field() -> str:
            return "x"

        @bramble.field
        def new_field() -> str:
            return "y"

    schema = bramble.Schema(query=Query)

    assert 'oldField: String! @deprecated(reason: "use newField")' in schema.to_sdl()

    result = schema.execute(
        '{ __type(name: "Query") { fields(includeDeprecated: true) { name isDeprecated deprecationReason } } }'
    )
    fields = {f["name"]: f for f in result["data"]["__type"]["fields"]}
    assert fields["oldField"]["isDeprecated"] is True
    assert fields["oldField"]["deprecationReason"] == "use newField"
    assert fields["newField"]["isDeprecated"] is False
    assert fields["newField"]["deprecationReason"] is None


def test_deprecated_fields_are_hidden_from_introspection_by_default() -> None:
    @bramble.type
    class Query:
        @bramble.field(deprecation_reason="gone")
        def old_field() -> str:
            return "x"

        @bramble.field
        def new_field() -> str:
            return "y"

    schema = bramble.Schema(query=Query)
    result = schema.execute('{ __type(name: "Query") { fields { name } } }')
    names = {f["name"] for f in result["data"]["__type"]["fields"]}

    assert "newField" in names
    assert "oldField" not in names, "includeDeprecated defaults to false"


def test_a_deprecated_field_is_still_queryable() -> None:
    # Deprecation is advisory: it must not stop the field working.
    @bramble.type
    class Query:
        @bramble.field(deprecation_reason="gone")
        def old_field() -> str:
            return "still here"

    assert bramble.Schema(query=Query).execute("{ oldField }") == {"data": {"oldField": "still here"}}


def test_subscription_is_an_alias_for_field() -> None:
    @bramble.type
    class Query:
        @bramble.field
        def ok() -> bool:
            return True

    @bramble.type
    class Subscription:
        @bramble.subscription
        async def ticks() -> AsyncGenerator[int, None]:
            yield 1

    schema = bramble.Schema(query=Query, subscription=Subscription)

    assert "ticks: Int!" in schema.to_sdl()


def test_directive_and_location_markers_are_exported_from_the_package_root() -> None:
    # Previously these could only be reached via `bramble.directive` / `bramble.schema_directive`,
    # so no directive could be declared from the `bramble` import alone.
    assert bramble.DirectiveLocation.FIELD.value == "FIELD"
    assert bramble.Location.OBJECT.value == "OBJECT"
    assert bramble.DirectiveValue is not None
