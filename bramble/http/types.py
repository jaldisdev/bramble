from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence
from typing import Any, Literal

HTTPMethod = Literal["GET", "POST"]
QueryParams = Mapping[str, "str | Sequence[str] | None"]


@dataclasses.dataclass
class GraphQLRequestData:
    """One GraphQL-over-HTTP operation, already extracted from wherever it came from (a GET
    query-string, a single JSON body, one entry of a batched JSON array, or one entry of a
    multipart request's `operations`) -- framework/transport-agnostic from this point on.
    """

    query: str | None
    variables: dict[str, Any] | None
    operation_name: str | None
    extensions: dict[str, Any] | None = None
