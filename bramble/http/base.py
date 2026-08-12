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

import json
from collections.abc import Mapping
from typing import Any, Generic, Protocol, TypeVar

from bramble.http.exceptions import HTTPException
from bramble.http.ides import get_graphql_ide_html
from bramble.http.types import GraphQLRequestData, HTTPMethod, QueryParams

Request = TypeVar("Request", contravariant=True)

# The Automatic Persisted Queries protocol version this implements -- the only one Apollo has ever
# defined. A request advertising a different version is treated as a plain (non-APQ) request rather
# than rejected, so a future version can't turn into a hard failure against an old server.
_APQ_VERSION = 1


def persisted_query_hash(extensions: Mapping[str, Any] | None) -> str | None:
    """The `sha256Hash` from a request's `extensions.persistedQuery`, if it carries a well-formed
    one -- the Apollo APQ convention for asking a server to run a query by hash. Returns `None` for
    any request that isn't an APQ request at all (no extensions, no `persistedQuery`, an unknown
    protocol version, or a non-string hash), so callers can treat that uniformly as "an ordinary
    request that must carry its own query text".
    """
    if not extensions:
        return None
    persisted = extensions.get("persistedQuery")
    if not isinstance(persisted, Mapping):
        return None
    version = persisted.get("version")
    if version is not None and version != _APQ_VERSION:
        return None
    sha256_hash = persisted.get("sha256Hash")
    return sha256_hash if isinstance(sha256_hash, str) else None


class BaseRequestProtocol(Protocol):
    """Whatever a concrete framework's own request object is, it satisfies this structurally (no
    inheritance needed) as long as it exposes these three things -- the same shape `BaseView`'s
    request-shape logic needs regardless of which framework (ASGI now; Django/Flask/etc. later,
    per the roadmap) actually produced the request.
    """

    @property
    def query_params(self) -> QueryParams: ...

    @property
    def method(self) -> HTTPMethod: ...

    @property
    def headers(self) -> Mapping[str, str]: ...


class BaseView(Generic[Request]):
    """Framework-agnostic GraphQL-over-HTTP request-shape logic, shared by every concrete
    adapter. Deliberately holds nothing framework-specific (no raw request/response types) --
    those live in `AsyncBaseHTTPView` (this module's own subclass) and, one layer further out, in
    each concrete adapter (`bramble.adapters.starlette`, `bramble.adapters.asgi`, and later
    FastAPI/Flask/Django/etc.).
    """

    multipart_uploads_enabled: bool = False

    def should_render_graphql_ide(self, request: BaseRequestProtocol) -> bool:
        return (
            request.method == "GET"
            and request.query_params.get("query") is None
            and any(accepted in request.headers.get("accept", "") for accepted in ("text/html", "*/*"))
        )

    def is_request_allowed(self, request: BaseRequestProtocol) -> bool:
        return request.method in ("GET", "POST")

    def parse_json(self, data: str | bytes) -> Any:
        try:
            return json.loads(data)
        except json.JSONDecodeError as error:
            raise HTTPException(400, "Unable to parse request body as JSON") from error

    def parse_query_params(self, params: QueryParams) -> dict[str, Any]:
        result: dict[str, Any] = dict(params)
        if result.get("variables"):
            result["variables"] = self.parse_json(result["variables"])
        if result.get("extensions"):
            result["extensions"] = self.parse_json(result["extensions"])
        return result

    def request_data_from_dict(self, data: Any) -> GraphQLRequestData:
        if not isinstance(data, dict):
            raise HTTPException(400, "Request data must be a JSON object")
        query = data.get("query")
        extensions = data.get("extensions")
        # A missing `query` is only legal for an Automatic Persisted Queries request, which
        # deliberately sends the hash *instead of* the query text -- that being the entire point of
        # the protocol. Rejecting it here unconditionally (as this used to) made APQ unusable over
        # HTTP no matter what the schema supported.
        if query is None and persisted_query_hash(extensions) is None:
            raise HTTPException(400, "No GraphQL query found in the request")
        return GraphQLRequestData(
            query=query,
            variables=data.get("variables"),
            operation_name=data.get("operationName"),
            extensions=extensions,
        )

    def parse_batch(self, operations: list[Any], *, max_operations: int | None) -> list[GraphQLRequestData]:
        if max_operations is None:
            raise HTTPException(400, "Batching is not enabled")
        # An empty array used to pass straight through, and the view then indexed `[0]` on the
        # empty result -- an `IndexError` escaping as a 500 for what is plainly a malformed request.
        if not operations:
            raise HTTPException(400, "No GraphQL operations found in the request")
        if len(operations) > max_operations:
            raise HTTPException(400, "Too many operations")
        return [self.request_data_from_dict(operation) for operation in operations]

    def parse_multipart_operations(self, form: Mapping[str, Any]) -> list[GraphQLRequestData]:
        """Implements the GraphQL multipart request spec's own `operations`/`map` mapping
        algorithm -- agnostic of *how* `form` was actually parsed off the wire (multipart
        boundary parsing is delegated to each framework's own battle-tested form parser; this
        only ever operates on the already-extracted field values).

        `operations` is a single request object or a batch list, JSON-encoded, with `null`
        placeholders wherever a file upload argument belongs; `map` is JSON mapping each file
        part's own form-field name to a list of dotted/indexed paths into `operations` pointing at
        the placeholder(s) to replace with that file (the same file can be referenced from more
        than one path). Every other form field not named in `map` is itself a file part, matched
        by name.
        """
        if "operations" not in form:
            raise HTTPException(400, "No `operations` value found in the multipart request")

        raw_operations = form["operations"]
        operations: Any = self.parse_json(raw_operations) if isinstance(raw_operations, str) else raw_operations

        raw_map = form.get("map")
        file_map: dict[str, list[str]] = (self.parse_json(raw_map) if isinstance(raw_map, str) else raw_map) or {}

        for file_field_name, paths in file_map.items():
            if file_field_name not in form:
                raise HTTPException(400, f"File '{file_field_name}' referenced in `map` was not found")
            file_value = form[file_field_name]
            for path in paths:
                _set_path(operations, path, file_value)

        operations_list = operations if isinstance(operations, list) else [operations]
        if not operations_list:
            raise HTTPException(400, "No GraphQL operations found in the request")
        return [self.request_data_from_dict(operation) for operation in operations_list]

    @property
    def graphql_ide_html(self) -> str:
        return get_graphql_ide_html()


def _set_path(root: Any, path: str, value: Any) -> None:
    """`path` is a dot-separated string (e.g. `"variables.file"`, or `"1.variables.files.0"` for
    the second operation of a batch) -- walks every segment but the last, then replaces whatever
    the final segment currently points at (always the spec's own `null` placeholder) with `value`.

    Every segment comes from the client's own `map`, so a path that doesn't resolve is malformed
    input, not a server bug: `KeyError`/`IndexError`/`TypeError` (indexing something that isn't a
    container) and `ValueError` (a non-numeric index into a list) all become a 400 rather than
    escaping as an unhandled 500 out of a file-upload endpoint.
    """
    segments = path.split(".")
    try:
        target = root
        for segment in segments[:-1]:
            key: str | int = int(segment) if isinstance(target, list) else segment
            target = target[key]

        last_key: str | int = int(segments[-1]) if isinstance(target, list) else segments[-1]
        target[last_key] = value
    except (KeyError, IndexError, TypeError, ValueError) as error:
        raise HTTPException(400, f"Invalid `map` path '{path}' in the multipart request") from error
