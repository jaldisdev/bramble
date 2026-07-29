from __future__ import annotations

import enum
from typing import Any

from bramble._bramble import GraphQLError as _GraphQLError


class ErrorCode(enum.Enum):
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
    def __init__(
        self,
        message: str,
        *,
        code: ErrorCode,
        locations: list[tuple[int, int]] | None = None,
        path: list[str | int] | None = None,
        extensions: dict[str, Any] | None = None,
    ) -> None:
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
