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

"""Encodes GraphQL's incremental-delivery `multipart/mixed` framing (one payload per part: the
initial `{"data": ..., "hasNext": bool}`, then each `{"incremental": [...], "hasNext": bool}`
patch) from an async stream of already-computed payload dicts -- the one place every adapter's own
`create_multipart_response` hook (`bramble/http/async_base_view.py`) reuses, so none of them need
to know anything about GraphQL/JSON themselves, only how to stream raw bytes.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

MULTIPART_MIXED_BOUNDARY = "graphql"


def multipart_content_type(boundary: str = MULTIPART_MIXED_BOUNDARY) -> str:
    """The response's own top-level `Content-Type` header value -- not part of the framing
    `encode_multipart_stream` produces (that's body bytes only), since which header object a
    concrete adapter sets that on is inherently framework-specific.
    """
    return f'multipart/mixed; boundary="{boundary}"'


async def encode_multipart_stream(
    payloads: AsyncIterator[dict[str, Any]], *, boundary: str = MULTIPART_MIXED_BOUNDARY
) -> AsyncIterator[bytes]:
    """Frames each payload dict as its own `multipart/mixed` part --
    `--boundary\\r\\nContent-Type: application/json; charset=utf-8\\r\\n\\r\\n{json}\\r\\n` --
    yielding one chunk of encoded bytes per part, followed by the closing `--boundary--\\r\\n`
    once `payloads` is exhausted.
    """
    async for payload in payloads:
        yield (
            f"--{boundary}\r\n"
            "Content-Type: application/json; charset=utf-8\r\n"
            "\r\n"
            f"{json.dumps(payload)}\r\n"
        ).encode("utf-8")
    yield f"--{boundary}--\r\n".encode("utf-8")


__all__ = ["MULTIPART_MIXED_BOUNDARY", "encode_multipart_stream", "multipart_content_type"]
