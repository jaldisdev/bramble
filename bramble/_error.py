from __future__ import annotations

import enum
from typing import Any

from bramble._bramble import GraphQLError as _GraphQLError


class ErrorCode(enum.Enum):
    GRAPHQL_PARSE_FAILED = "GRAPHQL_PARSE_FAILED"
    GRAPHQL_VALIDATION_FAILED = "GRAPHQL_VALIDATION_FAILED"
    INTERFACE_TYPE_RESOLUTION_FAILED = "INTERFACE_TYPE_RESOLUTION_FAILED"
    UNION_TYPE_RESOLUTION_FAILED = "UNION_TYPE_RESOLUTION_FAILED"


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
