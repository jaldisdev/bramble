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

import enum
from typing import Any

from bramble._bramble import GraphQLError as _GraphQLError


class ErrorCode(enum.Enum):
    """The `extensions.code` values bramble reports, so a client can branch on the kind of failure
    without matching on message text.

    Shared verbatim with the Rust core (`bramble_core::error::ErrorCode`), so a parse, validation,
    or execution failure is reported under the same name wherever it originated.
    """

    GRAPHQL_PARSE_FAILED = "GRAPHQL_PARSE_FAILED"
    GRAPHQL_VALIDATION_FAILED = "GRAPHQL_VALIDATION_FAILED"
    INTERFACE_TYPE_RESOLUTION_FAILED = "INTERFACE_TYPE_RESOLUTION_FAILED"
    UNION_TYPE_RESOLUTION_FAILED = "UNION_TYPE_RESOLUTION_FAILED"
    UNKNOWN_FIELD = "UNKNOWN_FIELD"
    UNKNOWN_ARGUMENT = "UNKNOWN_ARGUMENT"
    ARGUMENT_TYPE_MISMATCH = "ARGUMENT_TYPE_MISMATCH"
    INVALID_DIRECTIVE_LOCATION = "INVALID_DIRECTIVE_LOCATION"
    INVALID_FRAGMENT_TARGET = "INVALID_FRAGMENT_TARGET"
    PERSISTED_QUERY_NOT_FOUND = "PERSISTED_QUERY_NOT_FOUND"
    PERSISTED_QUERY_MISMATCH = "PERSISTED_QUERY_MISMATCH"
    FIELD_RESOLUTION_FAILED = "FIELD_RESOLUTION_FAILED"


class GraphQLError(_GraphQLError):
    """A GraphQL error, carrying the structured detail the spec's error shape allows.

    Raise one from a resolver to produce a field-level error with a code and extensions of your
    choosing:

        raise bramble.GraphQLError(
            f"no such user \'{user_id}\'",
            code=bramble.ErrorCode.FIELD_RESOLUTION_FAILED,
            extensions={"userId": user_id},
        )

    bramble fills in `path` and `locations` -- a resolver has no way to know its own position in
    the response. Any other exception a resolver raises is wrapped as a generic field error
    instead, so raising this is how you keep control of the message, code, and extensions.

    Also raised directly by `validate_query`, `execute*`, and `resolve_persisted_query` for
    request-level failures (a malformed query, an unknown operation, an APQ miss), which are
    reported as the whole response rather than one field's error.

    `code` is reported as `extensions.code`, which is where the wider ecosystem looks for a
    machine-readable error code -- Apollo Server populates exactly that key, and `ErrorCode`'s
    vocabulary matches it, so a client or tool reading it gets what it expects.

    A `code` entry in `extensions` takes precedence over the `code` argument, which is how you
    publish an application-specific code under the conventional key. Note that bramble's own
    `ErrorCode` is then absent from the response entirely, so anything keying off the standard
    values stops recognising that error; give the application code its own key instead if you want
    both.
    """

    def __init__(
        self,
        message: str,
        *,
        code: ErrorCode,
        locations: list[tuple[int, int]] | None = None,
        path: list[str | int] | None = None,
        extensions: dict[str, Any] | None = None,
    ) -> None:
        """Arguments:
        message: the human-readable error message.
        code: an `ErrorCode`, reported under `extensions.code`.
        locations: source positions in the query. Overwritten by the executor for a field error.
        path: the response path this error belongs to. Overwritten by the executor.
        extensions: extra keys merged alongside `code` in the response's `extensions` object. A
            `code` key here overrides the `code` argument -- see the class docstring.
        """
        super().__init__(message)
        self.message = message
        self.code = code
        self.locations = locations
        self.path = path
        self.extensions = extensions or {}


def error_to_dict(error: GraphQLError) -> dict[str, Any]:
    """Renders `error` in the GraphQL-over-HTTP spec's own error shape -- shared by the execution
    engine's own per-field errors and the HTTP layer's own request-level ones (a malformed query,
    an unsupported content type, ...), so both look identical to a client.
    """
    result: dict[str, Any] = {"message": error.message}
    if error.locations:
        result["locations"] = [{"line": line, "column": column} for line, column in error.locations]
    if error.path is not None:
        result["path"] = error.path
    result["extensions"] = {"code": error.code.value, **error.extensions}
    return result
