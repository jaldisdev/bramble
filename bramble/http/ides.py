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

import pathlib

_GRAPHIQL_HTML_PATH = pathlib.Path(__file__).parent / "static" / "graphiql.html"


def get_graphql_ide_html() -> str:
    """The GraphiQL page served on a browser `GET` with no `query` parameter -- a small static
    HTML file loading GraphiQL's own UMD build from a CDN at browser runtime, not a vendored JS
    bundle bramble ships or builds itself.
    """
    return _GRAPHIQL_HTML_PATH.read_text(encoding="utf-8")
