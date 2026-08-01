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

"""The actual ASGI app `bramble dev` hands to uvicorn, as the module string
`"bramble.cli.dev_server:app"` -- not imported directly by `bramble.cli.commands.dev` itself,
since uvicorn's `reload=True` re-imports this module fresh in its own (sub)process on every
reload, and a plain Python object/closure couldn't survive that. The schema selector and app
directory cross that process boundary via environment variables instead (set once, right before
`uvicorn.run` is called).
"""

from __future__ import annotations

import os

from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.routing import BaseRoute, Route, WebSocketRoute

from bramble.adapters.starlette import GraphQL
from bramble.cli.constants import DEV_SERVER_APP_DIR_ENV_VAR_KEY, DEV_SERVER_SCHEMA_ENV_VAR_KEY
from bramble.cli.utils import load_schema

_schema_selector = os.environ[DEV_SERVER_SCHEMA_ENV_VAR_KEY]
_app_dir = os.environ.get(DEV_SERVER_APP_DIR_ENV_VAR_KEY, ".")

schema = load_schema(_schema_selector, app_dir=_app_dir)
graphql_app = GraphQL(schema)

routes: list[BaseRoute] = []
for path in ("/", "/graphql"):
    routes.append(Route(path, graphql_app))
    routes.append(WebSocketRoute(path, graphql_app))

app = Starlette(debug=True, routes=routes)
app.add_middleware(CORSMiddleware, allow_headers=["*"], allow_origins=["*"], allow_methods=["*"])
