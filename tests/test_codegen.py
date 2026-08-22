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

from typing import Annotated, Union

import pytest

import bramble
from bramble.codegen import (
    ListType,
    NamedType,
    ObjectField,
    OptionalType,
    PythonPlugin,
    QueryCodegenError,
    TypeScriptPlugin,
    generate_operation,
)

# `typing.get_type_hints` can't see an enclosing test function's local scope, so any class
# referenced *from another class's annotation* has to live at module level here -- matches the
# rest of the test suite's own established convention.


# No leading underscore -- this class's own name is also its default GraphQL name, and it's
# referenced by that exact name from query text below (`$filter: PostFilter`).
@bramble.input
class PostFilter:
    limit: int = 10


@bramble.type
class _Author:
    name: str


@bramble.type
class _Post:
    title: str
    author: _Author


@bramble.type
class _Query:
    @bramble.field
    def post_by_slug(slug: str) -> _Post | None:
        return None

    @bramble.field
    def posts(filter: PostFilter) -> list[_Post]:
        return []


@bramble.interface
class _Media:
    id: bramble.ID


@bramble.type
class _Photo(_Media):
    width: int


@bramble.type
class _Video(_Media):
    duration: int


def _resolve_attachment(value: object, info: object) -> type:
    return _Photo if isinstance(value, _Photo) else _Video


_Attachment = Annotated[Union[_Photo, _Video], bramble.union("_Attachment", resolve_type=_resolve_attachment)]


@bramble.type
class _Gallery:
    @bramble.field
    def media() -> _Media:
        return _Photo(id="1", width=1)

    @bramble.field
    def attachment() -> _Attachment:
        return _Photo(id="1", width=1)


def _polymorphic_schema() -> bramble.Schema:
    return bramble.Schema(query=_Gallery, types=[_Media, _Photo, _Video])


def _schema() -> bramble.Schema:
    return bramble.Schema(query=_Query, types=[_Post, _Author])


def test_generates_a_flat_result_shape() -> None:
    operation = generate_operation(_schema(), "query GetPost($slug: String!) { postBySlug(slug: $slug) { title } }")

    assert operation.name == "GetPost"
    assert operation.operation_type == "query"
    assert operation.result_type.name == "GetPostResult"
    assert operation.result_type.fields == (
        ObjectField("postBySlug", OptionalType(NamedType("GetPostResultPostBySlug"))),
    )
    nested = next(shape for shape in operation.nested_types if shape.name == "GetPostResultPostBySlug")
    assert nested.fields == (ObjectField("title", NamedType("String")),)


def test_generates_nested_object_and_list_shapes() -> None:
    operation = generate_operation(
        _schema(), "query GetPosts($filter: PostFilter) { posts(filter: $filter) { title author { name } } }"
    )

    result_field = operation.result_type.fields[0]
    assert result_field.name == "posts"
    assert result_field.type == ListType(NamedType("GetPostsResultPosts"))

    posts_shape = next(shape for shape in operation.nested_types if shape.name == "GetPostsResultPosts")
    assert posts_shape.fields[0] == ObjectField("title", NamedType("String"))
    assert posts_shape.fields[1] == ObjectField("author", NamedType("GetPostsResultPostsAuthor"))

    author_shape = next(shape for shape in operation.nested_types if shape.name == "GetPostsResultPostsAuthor")
    assert author_shape.fields == (ObjectField("name", NamedType("String")),)


def test_generates_variable_definitions_including_input_types() -> None:
    operation = generate_operation(
        _schema(), "query GetPosts($filter: PostFilter) { posts(filter: $filter) { title } }"
    )

    assert operation.variables == (
        bramble.codegen.VariableDefinition("filter", OptionalType(NamedType("PostFilter")), optional=True),
    )
    input_shape = next(shape for shape in operation.nested_types if shape.name == "PostFilter")
    assert input_shape.fields == (ObjectField("limit", NamedType("Int"), optional=True),)


def test_required_scalar_variable_has_no_optional_wrapper() -> None:
    operation = generate_operation(_schema(), "query GetPost($slug: String!) { postBySlug(slug: $slug) { title } }")

    assert operation.variables == (bramble.codegen.VariableDefinition("slug", NamedType("String")),)


def test_typename_is_included_as_a_string_field() -> None:
    operation = generate_operation(_schema(), "query GetPost($slug: String!) { postBySlug(slug: $slug) { __typename title } }")

    nested = next(shape for shape in operation.nested_types if shape.name == "GetPostResultPostBySlug")
    assert nested.fields[0] == ObjectField("__typename", NamedType("String"))


def test_named_fragment_is_flattened_into_the_selection() -> None:
    query = """
    query GetPost($slug: String!) {
      postBySlug(slug: $slug) {
        ...PostFields
      }
    }

    fragment PostFields on _Post {
      title
      author { name }
    }
    """
    operation = generate_operation(_schema(), query)

    nested = next(shape for shape in operation.nested_types if shape.name == "GetPostResultPostBySlug")
    assert [field.name for field in nested.fields] == ["title", "author"]


def test_aliased_field_uses_the_alias_as_its_response_key() -> None:
    operation = generate_operation(_schema(), 'query GetPost($slug: String!) { post: postBySlug(slug: $slug) { heading: title } }')

    assert operation.result_type.fields[0].name == "post"
    nested = next(shape for shape in operation.nested_types if shape.name == "GetPostResultPost")
    assert nested.fields == (ObjectField("heading", NamedType("String")),)


def test_rejects_a_query_file_with_more_than_one_operation() -> None:
    query = "query A { postBySlug(slug: \"x\") { title } } query B { postBySlug(slug: \"y\") { title } }"

    with pytest.raises(QueryCodegenError, match="exactly one operation"):
        generate_operation(_schema(), query)


def test_rejects_an_unnamed_operation() -> None:
    with pytest.raises(QueryCodegenError, match="requires every operation to be named"):
        generate_operation(_schema(), '{ postBySlug(slug: "x") { title } }')


def test_rejects_an_undefined_fragment() -> None:
    with pytest.raises(QueryCodegenError, match="undefined fragment"):
        generate_operation(_schema(), 'query GetPost { postBySlug(slug: "x") { ...Missing } }')


def test_rejects_an_unknown_field() -> None:
    with pytest.raises(QueryCodegenError, match="does not exist"):
        generate_operation(_schema(), 'query GetPost { postBySlug(slug: "x") { doesNotExist } }')


def test_python_plugin_generates_valid_executable_code() -> None:
    operation = generate_operation(
        _schema(), "query GetPost($slug: String!) { postBySlug(slug: $slug) { title author { name } } }"
    )
    code = PythonPlugin().generate_code(operation)

    namespace: dict[str, object] = {}
    exec(compile(code, "<generated>", "exec"), namespace)  # noqa: S102 -- verifying our own generated code compiles/runs.

    author = namespace["GetPostResultPostBySlugAuthor"](name="Ada")
    post = namespace["GetPostResultPostBySlug"](title="Hello", author=author)
    result = namespace["GetPostResult"](postBySlug=post)
    assert result.postBySlug.author.name == "Ada"


def test_typescript_plugin_generates_expected_shape() -> None:
    operation = generate_operation(_schema(), "query GetPost($slug: String!) { postBySlug(slug: $slug) { title } }")
    code = TypeScriptPlugin().generate_code(operation)

    assert "export type GetPostResult = {" in code
    assert "postBySlug: GetPostResultPostBySlug | null;" in code
    assert "export type GetPostResultPostBySlug = {" in code
    assert "title: string;" in code


def test_interface_field_resolves_against_the_interface() -> None:
    operation = generate_operation(_polymorphic_schema(), "query G { media { id } }")

    nested = next(shape for shape in operation.nested_types if shape.name == "GResultMedia")
    assert [field.name for field in nested.fields] == ["id"]


def test_type_conditional_field_is_optional() -> None:
    operation = generate_operation(
        _polymorphic_schema(), "query G { media { id ... on _Photo { width } } }"
    )

    nested = next(shape for shape in operation.nested_types if shape.name == "GResultMedia")
    by_name = {field.name: field.type for field in nested.fields}

    assert list(by_name) == ["id", "width"]
    # `width` only exists when the value really is a `_Photo`
    assert isinstance(by_name["width"], OptionalType)
    assert not isinstance(by_name["id"], OptionalType)


def test_named_fragment_carries_its_own_condition() -> None:
    operation = generate_operation(
        _polymorphic_schema(),
        "query G { media { id ...PhotoFields } } fragment PhotoFields on _Photo { width }",
    )

    nested = next(shape for shape in operation.nested_types if shape.name == "GResultMedia")
    by_name = {field.name: field.type for field in nested.fields}
    assert isinstance(by_name["width"], OptionalType)


def test_union_members_contribute_their_own_fields() -> None:
    operation = generate_operation(
        _polymorphic_schema(),
        "query G { attachment { __typename ... on _Photo { width } ... on _Video { duration } } }",
    )

    nested = next(shape for shape in operation.nested_types if shape.name == "GResultAttachment")
    by_name = {field.name: field.type for field in nested.fields}

    assert list(by_name) == ["__typename", "width", "duration"]
    assert isinstance(by_name["width"], OptionalType)
    assert isinstance(by_name["duration"], OptionalType)


def test_selecting_a_field_straight_off_a_union_is_rejected() -> None:
    with pytest.raises(QueryCodegenError) as error:
        generate_operation(_polymorphic_schema(), "query G { attachment { width } }")

    message = str(error.value)
    assert "union '_Attachment'" in message
    assert "... on <Type>" in message


def test_unknown_fragment_condition_is_rejected() -> None:
    with pytest.raises(QueryCodegenError, match="unknown type '_Missing'"):
        generate_operation(_polymorphic_schema(), "query G { media { id ... on _Missing { x } } }")


def test_a_field_selected_both_ways_stays_required() -> None:
    operation = generate_operation(
        _polymorphic_schema(), "query G { media { id ... on _Photo { id } } }"
    )

    nested = next(shape for shape in operation.nested_types if shape.name == "GResultMedia")
    by_name = {field.name: field.type for field in nested.fields}

    assert list(by_name) == ["id"]
    assert not isinstance(by_name["id"], OptionalType)


def test_a_variable_is_omittable_unless_it_is_non_null_without_a_default() -> None:
    operation = generate_operation(
        _schema(),
        "query GetPosts($slug: String!, $filter: PostFilter) { postBySlug(slug: $slug) { title } }",
    )

    optional_by_name = {variable.name: variable.optional for variable in operation.variables}
    assert optional_by_name == {"slug": False, "filter": True}


def test_an_input_field_with_a_default_is_omittable() -> None:
    operation = generate_operation(
        _schema(), "query GetPosts($filter: PostFilter) { posts(filter: $filter) { title } }"
    )

    input_shape = next(shape for shape in operation.nested_types if shape.name == "PostFilter")
    assert [(field.name, field.optional) for field in input_shape.fields] == [("limit", True)]


def test_a_selected_result_field_is_never_omittable() -> None:
    """A field the client asked for comes back in the response even when its value is null, so
    nullability must not leak into whether the key is present.
    """
    operation = generate_operation(_schema(), "query GetPosts { posts { title } }")

    assert all(not field.optional for field in operation.result_type.fields)


def test_typescript_marks_only_omittable_members_with_a_question_mark() -> None:
    operation = generate_operation(
        _schema(),
        "query GetPosts($slug: String!, $filter: PostFilter) { postBySlug(slug: $slug) { title } }",
    )
    code = bramble.codegen.TypeScriptPlugin().generate_code(operation)

    assert "  slug: string;" in code
    assert "  filter?: PostFilter | null;" in code
    assert "  limit?: number;" in code


def test_python_defaults_trail_the_required_members() -> None:
    operation = generate_operation(
        _schema(),
        "query GetPosts($filter: PostFilter, $slug: String!) { postBySlug(slug: $slug) { title } }",
    )
    code = bramble.codegen.PythonPlugin().generate_code(operation)

    assert code.index("    slug: str") < code.index("    filter: PostFilter | None = None")
