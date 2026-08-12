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

import io

from flask import Flask
from flask.testing import FlaskClient

import bramble
from bramble.adapters.flask import graphql_view
from bramble.schema.config import SchemaConfig

# Flask is WSGI-based (no WebSocket support at all -- see `bramble/adapters/flask/__init__.py`'s
# own docstring), so these tests only cover HTTP, driven through Flask's own `test_client()`.


@bramble.type
class _Query:
    @bramble.field
    def greet(name: str = "world") -> str:
        return f"Hello, {name}!"

    @bramble.field
    async def upload_size(file: bramble.Upload) -> int:
        content = await file.read()  # type: ignore[attr-defined]
        return len(content)
    @bramble.field
    async def upload_meta(file: bramble.Upload) -> str:
        return f"{file.filename}|{file.content_type}"  # type: ignore[attr-defined]


@bramble.type
class _Author:
    name: str


@bramble.type
class _DeferrableQuery:
    @bramble.field
    def id() -> str:
        return "q1"

    @bramble.field
    def author() -> _Author:
        return _Author(name="Ada")


def _make_client(schema: bramble.Schema) -> FlaskClient:
    app = Flask(__name__)
    app.register_blueprint(graphql_view(schema, path="/graphql"))
    return app.test_client()


def test_get_without_query_serves_graphiql() -> None:
    schema = bramble.Schema(query=_Query)
    client = _make_client(schema)

    response = client.get("/graphql", headers={"accept": "text/html"})

    assert response.status_code == 200
    assert "GraphiQL" in response.get_data(as_text=True)


def test_get_with_query_param_executes() -> None:
    schema = bramble.Schema(query=_Query)
    client = _make_client(schema)

    response = client.get("/graphql", query_string={"query": "{ greet }"})

    assert response.status_code == 200
    assert response.get_json() == {"data": {"greet": "Hello, world!"}}


def test_post_json_executes() -> None:
    schema = bramble.Schema(query=_Query)
    client = _make_client(schema)

    response = client.post(
        "/graphql", json={"query": "query($n: String) { greet(name: $n) }", "variables": {"n": "Ada"}}
    )

    assert response.status_code == 200
    assert response.get_json() == {"data": {"greet": "Hello, Ada!"}}


def test_post_batched_json_executes_each_operation() -> None:
    schema = bramble.Schema(query=_Query, config=SchemaConfig(batching_config={"max_operations": 5}))
    client = _make_client(schema)

    response = client.post(
        "/graphql",
        json=[
            {"query": 'query { greet(name: "A") }'},
            {"query": 'query { greet(name: "B") }'},
        ],
    )

    assert response.status_code == 200
    assert response.get_json() == [
        {"data": {"greet": "Hello, A!"}},
        {"data": {"greet": "Hello, B!"}},
    ]


def test_post_multipart_upload_executes() -> None:
    schema = bramble.Schema(query=_Query)
    client = _make_client(schema)

    response = client.post(
        "/graphql",
        data={
            "operations": '{"query": "query($f: Upload!) { uploadSize(file: $f) }", "variables": {"f": null}}',
            "map": '{"0": ["variables.f"]}',
            "0": (io.BytesIO(b"hello upload"), "greeting.txt"),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    assert response.get_json() == {"data": {"uploadSize": len(b"hello upload")}}


def test_an_upload_carries_its_filename_and_content_type() -> None:
    schema = bramble.Schema(query=_Query)
    client = _make_client(schema)

    response = client.post(
        "/graphql",
        data={
            "operations": '{"query": "query($f: Upload!) { uploadMeta(file: $f) }", "variables": {"f": null}}',
            "map": '{"0": ["variables.f"]}',
            "0": (io.BytesIO(b"hello upload"), "greeting.txt", "text/plain"),
        },
        content_type="multipart/form-data",
    )

    assert response.get_json() == {"data": {"uploadMeta": "greeting.txt|text/plain"}}


def test_defer_query_streams_a_multipart_mixed_response() -> None:
    schema = bramble.Schema(query=_DeferrableQuery, types=[_Author])
    client = _make_client(schema)

    response = client.post("/graphql", json={"query": "query { id ... @defer { author { name } } }"})

    assert response.status_code == 200
    assert response.headers["content-type"] == 'multipart/mixed; boundary="graphql"'
    body = response.get_data()
    assert body == (
        b"--graphql\r\n"
        b"Content-Type: application/json; charset=utf-8\r\n"
        b"\r\n"
        b'{"data": {"id": "q1"}, "hasNext": true}\r\n'
        b"--graphql\r\n"
        b"Content-Type: application/json; charset=utf-8\r\n"
        b"\r\n"
        b'{"incremental": [{"data": {"author": {"name": "Ada"}}, "path": []}], "hasNext": false}\r\n'
        b"--graphql--\r\n"
    )


def test_disallowed_method_returns_405() -> None:
    schema = bramble.Schema(query=_Query)
    client = _make_client(schema)

    response = client.delete("/graphql")

    assert response.status_code == 405


def test_no_websocket_route_is_registered() -> None:
    schema = bramble.Schema(query=_Query)
    app = Flask(__name__)
    app.register_blueprint(graphql_view(schema, path="/graphql"))

    # Flask/WSGI has no concept of a websocket route at all -- confirms the only rule registered
    # for this path is the plain HTTP GraphQL view, matching this adapter's documented HTTP-only
    # scope rather than silently missing WebSocket support.
    rules = [rule for rule in app.url_map.iter_rules() if rule.rule == "/graphql"]
    assert len(rules) == 1
    assert rules[0].methods is not None
    assert rules[0].methods >= {"GET", "POST"}
