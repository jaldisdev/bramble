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


def parse_content_type(value: str) -> tuple[str, dict[str, str]]:
    """Splits a `Content-Type` header into its bare MIME type and any `key=value` parameters
    (e.g. `multipart/form-data; boundary=----abc` -> `("multipart/form-data", {"boundary": "----abc"})`).
    Parameter values are only ever unquoted here (never otherwise decoded) -- the one parameter
    bramble's own HTTP layer actually reads (`boundary`) never needs anything more.
    """
    parts = value.split(";")
    mime_type = parts[0].strip().lower()

    params: dict[str, str] = {}
    for part in parts[1:]:
        if "=" not in part:
            continue
        key, _, raw_value = part.partition("=")
        params[key.strip().lower()] = raw_value.strip().strip('"')

    return mime_type, params
