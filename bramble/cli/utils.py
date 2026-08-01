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

import importlib
import sys
from typing import Any

import click

from bramble._schema import Schema


def _import_module_symbol(selector: str, *, default_symbol_name: str) -> Any:
    """`selector` is either `module.path:symbol.attr` or just `module.path` (in which case
    `default_symbol_name` -- always `"schema"` for bramble's own CLI commands -- is looked up on
    it instead).
    """
    if ":" in selector:
        module_name, symbol_name = selector.split(":", 1)
    else:
        module_name, symbol_name = selector, default_symbol_name

    module = importlib.import_module(module_name)
    symbol: Any = module
    for attribute_name in symbol_name.split("."):
        symbol = getattr(symbol, attribute_name)
    return symbol


def load_schema(schema: str, *, app_dir: str) -> Schema:
    """Resolves `schema` (a `module.path:symbol` selector, e.g. `myapp.schema:schema`) to a real
    `bramble.Schema` instance -- `app_dir` is added to `sys.path` first, so the module can be
    found the same way it would be if `myapp` were installed. If the resolved symbol is callable
    (a factory function, not the `Schema` itself) it's called with no arguments to build one.
    """
    sys.path.insert(0, app_dir)

    try:
        symbol = _import_module_symbol(schema, default_symbol_name="schema")
    except (ImportError, AttributeError) as error:
        raise click.ClickException(str(error)) from error

    if callable(symbol):
        try:
            symbol = symbol()
        except Exception as error:  # noqa: BLE001 -- any failure building the schema is the user's to fix, not ours to hide.
            raise click.ClickException(f"Error invoking schema symbol: {error}") from error

    if not isinstance(symbol, Schema):
        raise click.ClickException(f"'{schema}' must resolve to an instance of bramble.Schema")

    return symbol
