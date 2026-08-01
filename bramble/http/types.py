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
