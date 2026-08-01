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


class HTTPException(Exception):
    """A request-shape problem the transport layer itself must reject (bad JSON, an unsupported
    content type, a missing query, a disallowed method, ...) -- distinct from a `GraphQLError`,
    which describes a problem with the *GraphQL operation itself* once execution is already
    underway. Each concrete adapter (ASGI, ...) is responsible for turning this into whatever its
    own framework's error response looks like.
    """

    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        self.message = message
        super().__init__(message)
