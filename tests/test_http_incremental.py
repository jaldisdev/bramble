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
from collections.abc import AsyncIterator
from typing import Any

import bramble
from bramble.http.async_base_view import AsyncBaseHTTPView
from bramble.http.multipart import encode_multipart_stream

# `bramble.http` is framework-agnostic by design -- these tests exercise the multipart framing
# helper directly, and `AsyncBaseHTTPView.run()`'s own dispatch through a minimal fake request/
# adapter, with no real ASGI server involved (that's each concrete adapter's own test file's job).


def _run(coro):  # type: ignore[no-untyped-def]
    return asyncio.run(coro)


async def _collect_bytes(stream: AsyncIterator[bytes]) -> bytes:
    return b"".join([chunk async for chunk in stream])


async def _three_payloads() -> AsyncIterator[dict[str, Any]]:
    yield {"data": {"id": "q1"}, "hasNext": True}
    yield {"incremental": [{"data": {"a": 1}, "path": []}], "hasNext": True}
    yield {"incremental": [{"data": {"b": 2}, "path": []}], "hasNext": False}


def test_encode_multipart_stream_frames_each_payload_and_closes_the_boundary() -> None:
    encoded = _run(_collect_bytes(encode_multipart_stream(_three_payloads(), boundary="test")))

    assert encoded == (
        b"--test\r\n"
        b"Content-Type: application/json; charset=utf-8\r\n"
        b"\r\n"
        b'{"data": {"id": "q1"}, "hasNext": true}\r\n'
        b"--test\r\n"
        b"Content-Type: application/json; charset=utf-8\r\n"
        b"\r\n"
        b'{"incremental": [{"data": {"a": 1}, "path": []}], "hasNext": true}\r\n'
        b"--test\r\n"
        b"Content-Type: application/json; charset=utf-8\r\n"
        b"\r\n"
        b'{"incremental": [{"data": {"b": 2}, "path": []}], "hasNext": false}\r\n'
        b"--test--\r\n"
    )


def test_encode_multipart_stream_with_no_payloads_still_closes_the_boundary() -> None:
    async def _empty() -> AsyncIterator[dict[str, Any]]:
        return
        yield  # pragma: no cover -- makes this an async generator

    encoded = _run(_collect_bytes(encode_multipart_stream(_empty(), boundary="x")))
    assert encoded == b"--x--\r\n"


@bramble.type
class _Author:
    name: str


@bramble.type
class _Query:
    @bramble.field
    def id() -> str:
        return "q1"

    @bramble.field
    def author() -> _Author:
        return _Author(name="Ada")

    @bramble.field
    def greet(name: str = "world") -> str:
        return f"Hello, {name}!"


class _FakeRequest:
    def __init__(
        self,
        *,
        method: str = "POST",
        query_params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        body: bytes = b"",
    ) -> None:
        self.method = method
        self.query_params = query_params or {}
        self.headers = headers or {}
        self.body = body


class _FakeView(AsyncBaseHTTPView[_FakeRequest, dict, Any, Any]):
    def __init__(self, schema: bramble.Schema) -> None:
        self.schema = schema

    async def get_body(self, request: _FakeRequest) -> bytes:
        return request.body

    async def get_form_data(self, request: _FakeRequest) -> dict[str, object]:
        return {}

    async def get_context(self, request: _FakeRequest) -> None:
        return None

    def create_response(self, response_data: object) -> dict:
        return {"kind": "json", "body": response_data}

    def create_html_response(self, html: str) -> dict:
        return {"kind": "html", "body": html}

    async def create_multipart_response(self, stream: AsyncIterator[bytes]) -> dict:
        return {"kind": "multipart", "body": await _collect_bytes(stream)}

    def is_websocket_request(self, request: _FakeRequest) -> bool:
        return False


schema = bramble.Schema(query=_Query, types=[_Author])


def test_run_dispatches_a_defer_query_to_the_multipart_response() -> None:
    view = _FakeView(schema)
    body = b'{"query": "query { id ... @defer { author { name } } }"}'
    request = _FakeRequest(headers={"content-type": "application/json"}, body=body)

    response = _run(view.run(request, request))

    assert response["kind"] == "multipart"
    body_bytes = response["body"]
    assert b'"data": {"id": "q1"}, "hasNext": true' in body_bytes
    assert b'"author": {"name": "Ada"}' in body_bytes
    assert body_bytes.endswith(b"--graphql--\r\n")


def test_run_leaves_a_plain_query_on_the_normal_json_response_path() -> None:
    view = _FakeView(schema)
    body = b'{"query": "{ greet }"}'
    request = _FakeRequest(headers={"content-type": "application/json"}, body=body)

    response = _run(view.run(request, request))

    assert response == {"kind": "json", "body": {"data": {"greet": "Hello, world!"}}}


def test_run_leaves_a_get_query_using_defer_on_the_normal_json_response_path() -> None:
    """Incremental delivery is POST-only (§ scope notes) -- a GET query using `@defer` never even
    reaches the multipart dispatch, so it falls through to the normal single-shot path, which
    itself still correctly rejects `@defer`/`@stream` (`execute_async`'s own pre-check) as a
    regular JSON error response rather than crashing or silently streaming.
    """
    view = _FakeView(schema)
    request = _FakeRequest(method="GET", query_params={"query": "query { id ... @defer { author { name } } }"})

    response = _run(view.run(request, request))

    assert response["kind"] == "json"
    assert response["body"]["data"] is None
    assert "execute_incremental" in response["body"]["errors"][0]["message"]
