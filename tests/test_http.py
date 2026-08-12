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
import hashlib
import json
from typing import Any

import pytest

import bramble
import bramble._execution
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


# --- Automatic Persisted Queries ------------------------------------------------------------------


def _apq_body(sha256_hash: str, *, query: str | None = None, variables: dict | None = None) -> bytes:
    body: dict[str, object] = {"extensions": {"persistedQuery": {"version": 1, "sha256Hash": sha256_hash}}}
    if query is not None:
        body["query"] = query
    if variables is not None:
        body["variables"] = variables
    return json.dumps(body).encode()


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _post(view: _FakeView, body: bytes) -> dict:
    request = _FakeRequest(headers={"content-type": "application/json"}, body=body)
    return _run(view.run(request, request))


def test_hash_only_request_for_an_unregistered_query_returns_the_apq_miss_error() -> None:
    """The miss must come back as a normal response body, not an HTTP error: Apollo Client's APQ
    link matches on this exact message to trigger its resend-with-full-query retry, which it never
    gets to do if the request fails at the status level. This used to be an unconditional 400.
    """
    view = _FakeView(bramble.Schema(query=_Query))
    query = "{ greet }"

    response = _post(view, _apq_body(_sha256(query)))

    assert response["kind"] == "json"
    assert response["body"]["data"] is None
    assert response["body"]["errors"][0]["message"] == "PersistedQueryNotFound"


def test_registering_then_replaying_a_persisted_query_over_http() -> None:
    view = _FakeView(bramble.Schema(query=_Query))
    query = 'query { greet(name: "Ada") }'
    sha256_hash = _sha256(query)

    registration = _post(view, _apq_body(sha256_hash, query=query))
    assert registration["body"] == {"data": {"greet": "Hello, Ada!"}}

    replay = _post(view, _apq_body(sha256_hash))
    assert replay["body"] == {"data": {"greet": "Hello, Ada!"}}


def test_replaying_a_persisted_query_does_not_reparse_or_revalidate_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole point of caching the *parsed and validated* document. Before this, a hash-only
    replay still went through `validate_query` (which parses again internally) on every request, so
    the cache bought nothing but the protocol handshake.
    """
    view = _FakeView(bramble.Schema(query=_Query))
    query = 'query { greet(name: "Ada") }'
    sha256_hash = _sha256(query)

    _post(view, _apq_body(sha256_hash, query=query))

    calls: list[str] = []
    real_validate = bramble._execution.validate_query

    def spy(query_text: str, compiled: object, operation_name: str | None) -> None:
        calls.append(query_text)
        return real_validate(query_text, compiled, operation_name)

    monkeypatch.setattr(bramble._execution, "validate_query", spy)

    replay = _post(view, _apq_body(sha256_hash))

    assert replay["body"] == {"data": {"greet": "Hello, Ada!"}}
    assert calls == [], "a persisted-query replay must not re-validate (and so must not re-parse)"


def test_a_non_persisted_request_still_validates_normally(monkeypatch: pytest.MonkeyPatch) -> None:
    # The counterpart to the test above: the fast path must not have disabled validation generally.
    view = _FakeView(bramble.Schema(query=_Query))

    calls: list[str] = []
    real_validate = bramble._execution.validate_query

    def spy(query_text: str, compiled: object, operation_name: str | None) -> None:
        calls.append(query_text)
        return real_validate(query_text, compiled, operation_name)

    monkeypatch.setattr(bramble._execution, "validate_query", spy)

    _post(view, json.dumps({"query": "{ greet }"}).encode())

    assert calls == ["{ greet }"]


def test_a_persisted_replay_still_binds_this_requests_variables() -> None:
    """Lowering is deliberately *not* cached, only parse/validate -- `@skip`/`@include` and argument
    substitution depend on each request's own variables, so a replay must re-lower.
    """
    view = _FakeView(bramble.Schema(query=_Query))
    query = "query G($n: String!) { greet(name: $n) }"
    sha256_hash = _sha256(query)

    _post(view, _apq_body(sha256_hash, query=query, variables={"n": "Ada"}))
    replay = _post(view, _apq_body(sha256_hash, variables={"n": "Grace"}))

    assert replay["body"] == {"data": {"greet": "Hello, Grace!"}}


def test_a_hash_that_does_not_match_the_supplied_query_is_rejected() -> None:
    view = _FakeView(bramble.Schema(query=_Query))

    response = _post(view, _apq_body("0" * 64, query="{ greet }"))

    assert response["body"]["data"] is None
    assert "does not match" in response["body"]["errors"][0]["message"]


def test_an_unknown_apq_protocol_version_is_treated_as_an_ordinary_request() -> None:
    view = _FakeView(bramble.Schema(query=_Query))
    body = json.dumps(
        {"query": "{ greet }", "extensions": {"persistedQuery": {"version": 99, "sha256Hash": "x" * 64}}}
    ).encode()

    response = _post(view, body)

    assert response["body"] == {"data": {"greet": "Hello, world!"}}


def test_a_request_with_neither_query_nor_persisted_hash_is_still_a_400() -> None:
    view = _FakeView(bramble.Schema(query=_Query))
    request = _FakeRequest(headers={"content-type": "application/json"}, body=json.dumps({}).encode())

    with pytest.raises(HTTPException) as excinfo:
        _run(view.run(request, request))
    assert excinfo.value.status_code == 400


# --- Malformed input must be a 400, never a 500 ---------------------------------------------------


def test_multipart_map_path_that_does_not_resolve_is_a_400() -> None:
    view = _FakeView(bramble.Schema(query=_Query))
    request = _FakeRequest(
        headers={"content-type": "multipart/form-data"},
        form={
            "operations": json.dumps({"query": "mutation { echo(file: null) }", "variables": {}}),
            "map": json.dumps({"file": ["variables.nope.deeper"]}),
            "file": b"contents",
        },
    )

    with pytest.raises(HTTPException) as excinfo:
        _run(view.run(request, request))
    assert excinfo.value.status_code == 400
    assert "map" in excinfo.value.message


def test_multipart_map_path_indexing_a_non_list_is_a_400() -> None:
    view = _FakeView(bramble.Schema(query=_Query))
    request = _FakeRequest(
        headers={"content-type": "multipart/form-data"},
        form={
            "operations": json.dumps({"query": "mutation { echo(file: null) }", "variables": {"file": None}}),
            "map": json.dumps({"file": ["variables.file.0"]}),
            "file": b"contents",
        },
    )

    with pytest.raises(HTTPException) as excinfo:
        _run(view.run(request, request))
    assert excinfo.value.status_code == 400


def test_multipart_map_path_with_a_non_numeric_list_index_is_a_400() -> None:
    view = _FakeView(bramble.Schema(query=_Query))
    request = _FakeRequest(
        headers={"content-type": "multipart/form-data"},
        form={
            "operations": json.dumps({"query": "mutation { echo(file: null) }", "variables": {"files": [None]}}),
            "map": json.dumps({"file": ["variables.files.notanindex"]}),
            "file": b"contents",
        },
    )

    with pytest.raises(HTTPException) as excinfo:
        _run(view.run(request, request))
    assert excinfo.value.status_code == 400


def test_empty_json_batch_is_a_400_not_an_index_error() -> None:
    schema = bramble.Schema(query=_Query, config=SchemaConfig(batching_config={"max_operations": 5}))
    view = _FakeView(schema)
    request = _FakeRequest(headers={"content-type": "application/json"}, body=b"[]")

    with pytest.raises(HTTPException) as excinfo:
        _run(view.run(request, request))
    assert excinfo.value.status_code == 400
