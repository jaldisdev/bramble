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

import os
import sys

import click

from bramble.cli.constants import DEV_SERVER_APP_DIR_ENV_VAR_KEY, DEV_SERVER_SCHEMA_ENV_VAR_KEY
from bramble.cli.utils import load_schema

_LOG_LEVELS = ["critical", "error", "warning", "info", "debug", "trace"]


@click.command(help="Starts the development server")
@click.argument("schema", type=str)
@click.option("-h", "--host", default="0.0.0.0", show_default=True, help="Host to bind the server to.")  # noqa: S104
@click.option("-p", "--port", default=8000, show_default=True, type=int, help="Port to bind the server to.")
@click.option(
    "--log-level",
    default="error",
    show_default=True,
    type=click.Choice(_LOG_LEVELS),
    help="Passed to uvicorn to determine the server log level.",
)
@click.option(
    "--app-dir",
    default=".",
    show_default=True,
    help="Look for the schema module in the specified directory, by adding it to the PYTHONPATH.",
)
def dev(schema: str, host: str, port: int, log_level: str, app_dir: str) -> None:
    try:
        import starlette  # noqa: F401
        import uvicorn
    except ImportError:
        click.secho(
            "Error: the dev server requires additional packages, install them with:\n"
            '  pip install "bramble[cli]"',
            fg="red",
        )
        raise SystemExit(1) from None

    sys.path.insert(0, app_dir)
    load_schema(schema, app_dir=app_dir)  # fails fast with a clear error before uvicorn even starts

    os.environ[DEV_SERVER_SCHEMA_ENV_VAR_KEY] = schema
    os.environ[DEV_SERVER_APP_DIR_ENV_VAR_KEY] = app_dir

    click.secho(f"Running bramble on http://{host}:{port}/graphql", fg="green")

    uvicorn.run(
        "bramble.cli.dev_server:app",
        host=host,
        port=port,
        log_level=log_level,
        reload=True,
        reload_dirs=[app_dir],
    )
