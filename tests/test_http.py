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

import asyncio
import json
from typing import Any

import pytest

import bramble
from bramble.http.async_base_view import AsyncBaseHTTPView
from bramble.http.exceptions import HTTPException
from bramble.schema.config import SchemaConfig

# `bramble.http` is framework-agnostic by design -- these tests exercise `AsyncBaseHTTPView`
# directly through a minimal fake request/adapter, with no real ASGI server involved (that's
# `tests/test_adapters_starlette.py`'s job, against the concrete `bramble.adapters.starlette`
# adapter).


@bramble.type
class _Query:
    @bramble.field
    def greet(name: str = "world") -> str:
        return f"Hello, {name}!"

    @bramble.field
    def echo(file: bramble.Upload) -> bramble.Upload:
        return file


class _FakeRequest:
    def __init__(
        self,
        *,
        method: str = "POST",
        query_params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        body: bytes = b"",
        form: dict[str, object] | None = None,
    ) -> None:
        self.method = method
        self.query_params = query_params or {}
        self.headers = headers or {}
        self.body = body
        self.form = form or {}


class _FakeView(AsyncBaseHTTPView[_FakeRequest, dict, Any, Any]):
    multipart_uploads_enabled = True

    def __init__(self, schema: bramble.Schema) -> None:
        self.schema = schema

    async def get_body(self, request: _FakeRequest) -> bytes:
        return request.body

    async def get_form_data(self, request: _FakeRequest) -> dict[str, object]:
        return request.form

    async def get_context(self, request: _FakeRequest) -> None:
        return None

    def create_response(self, response_data: object) -> dict:
        return {"kind": "json", "body": response_data}

    def create_html_response(self, html: str) -> dict:
        return {"kind": "html", "body": html}

    def is_websocket_request(self, request: _FakeRequest) -> bool:
        return False


def _run(coro):  # type: ignore[no-untyped-def]
    return asyncio.run(coro)


def test_get_without_query_renders_graphql_ide() -> None:
    schema = bramble.Schema(query=_Query)
    view = _FakeView(schema)
    request = _FakeRequest(method="GET", headers={"accept": "text/html"})

    response = _run(view.run(request, request))

    assert response["kind"] == "html"
    assert "GraphiQL" in response["body"]


def test_get_with_query_param_executes() -> None:
    schema = bramble.Schema(query=_Query)
    view = _FakeView(schema)
    request = _FakeRequest(method="GET", query_params={"query": "{ greet }"})

    response = _run(view.run(request, request))

    assert response == {"kind": "json", "body": {"data": {"greet": "Hello, world!"}}}


def test_post_json_body_executes() -> None:
    schema = bramble.Schema(query=_Query)
    view = _FakeView(schema)
    body = json.dumps({"query": 'query { greet(name: "Ada") }'}).encode()
    request = _FakeRequest(headers={"content-type": "application/json"}, body=body)

    response = _run(view.run(request, request))

    assert response["body"] == {"data": {"greet": "Hello, Ada!"}}


def test_malformed_json_body_raises_http_exception() -> None:
    schema = bramble.Schema(query=_Query)
    view = _FakeView(schema)
    request = _FakeRequest(headers={"content-type": "application/json"}, body=b"not json")

    with pytest.raises(HTTPException) as excinfo:
        _run(view.run(request, request))
    assert excinfo.value.status_code == 400


def test_batching_disabled_by_default_rejects_array_body() -> None:
    schema = bramble.Schema(query=_Query)
    view = _FakeView(schema)
    body = json.dumps([{"query": "{ greet }"}]).encode()
    request = _FakeRequest(headers={"content-type": "application/json"}, body=body)

    with pytest.raises(HTTPException, match="Batching is not enabled"):
        _run(view.run(request, request))


def test_batching_enabled_executes_each_operation_in_order() -> None:
    schema = bramble.Schema(query=_Query, config=SchemaConfig(batching_config={"max_operations": 5}))
    view = _FakeView(schema)
    body = json.dumps(
        [
            {"query": 'query { greet(name: "A") }'},
            {"query": 'query { greet(name: "B") }'},
        ]
    ).encode()
    request = _FakeRequest(headers={"content-type": "application/json"}, body=body)

    response = _run(view.run(request, request))

    assert response["body"] == [
        {"data": {"greet": "Hello, A!"}},
        {"data": {"greet": "Hello, B!"}},
    ]


def test_batching_enabled_rejects_too_many_operations() -> None:
    schema = bramble.Schema(query=_Query, config=SchemaConfig(batching_config={"max_operations": 1}))
    view = _FakeView(schema)
    body = json.dumps([{"query": "{ greet }"}, {"query": "{ greet }"}]).encode()
    request = _FakeRequest(headers={"content-type": "application/json"}, body=body)

    with pytest.raises(HTTPException, match="Too many operations"):
        _run(view.run(request, request))


def test_multipart_upload_maps_file_into_variables() -> None:
    schema = bramble.Schema(query=_Query)
    view = _FakeView(schema)
    operations = json.dumps({"query": "query($f: Upload!) { echo(file: $f) }", "variables": {"f": None}})
    file_map = json.dumps({"0": ["variables.f"]})
    request = _FakeRequest(
        headers={"content-type": "multipart/form-data; boundary=x"},
        form={"operations": operations, "map": file_map, "0": b"file-bytes"},
    )

    response = _run(view.run(request, request))

    assert response["body"] == {"data": {"echo": b"file-bytes"}}


def test_multipart_disabled_rejects_multipart_content_type() -> None:
    schema = bramble.Schema(query=_Query)

    class _NoUploadView(_FakeView):
        multipart_uploads_enabled = False

    view = _NoUploadView(schema)
    request = _FakeRequest(headers={"content-type": "multipart/form-data; boundary=x"}, form={})

    with pytest.raises(HTTPException, match="File uploads are not enabled"):
        _run(view.run(request, request))


def test_disallowed_method_raises_http_exception() -> None:
    schema = bramble.Schema(query=_Query)
    view = _FakeView(schema)
    request = _FakeRequest(method="DELETE")

    with pytest.raises(HTTPException) as excinfo:
        _run(view.run(request, request))
    assert excinfo.value.status_code == 405


def test_execute_async_error_is_wrapped_in_the_response_body() -> None:
    schema = bramble.Schema(query=_Query)
    view = _FakeView(schema)
    body = json.dumps({"query": "{ doesNotExist }"}).encode()
    request = _FakeRequest(headers={"content-type": "application/json"}, body=body)

    response = _run(view.run(request, request))

    assert response["body"]["data"] is None
    assert "errors" in response["body"]
