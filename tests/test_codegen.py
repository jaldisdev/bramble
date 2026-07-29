from __future__ import annotations

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
        bramble.codegen.VariableDefinition("filter", OptionalType(NamedType("PostFilter"))),
    )
    input_shape = next(shape for shape in operation.nested_types if shape.name == "PostFilter")
    assert input_shape.fields == (ObjectField("limit", NamedType("Int")),)


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

    fragment PostFields on Post {
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
