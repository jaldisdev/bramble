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

"""Django integration -- an async HTTP view (`bramble.adapters.django.views.AsyncGraphQLView`) plus,
if `channels` is installed (bramble's own `django` extra pulls it in), a Channels-based WebSocket
consumer in `bramble.adapters.django.channels` (kept as a separate submodule, not imported here, so
importing `bramble.adapters.django` itself never requires `channels` to be installed).
"""

from __future__ import annotations

from bramble.adapters.django.views import AsyncGraphQLView, graphql_view

__all__ = ["AsyncGraphQLView", "graphql_view"]
