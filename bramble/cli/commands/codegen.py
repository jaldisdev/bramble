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
from pathlib import Path

import click

from bramble.cli.utils import load_schema
from bramble.codegen import QueryCodegenError, generate_operation
from bramble.codegen.plugins import QueryCodegenPlugin, get_builtin_plugin


def _load_plugin(selector: str) -> type[QueryCodegenPlugin]:
    builtin = get_builtin_plugin(selector)
    if builtin is not None:
        return builtin

    if ":" not in selector:
        raise click.ClickException(
            f"Plugin '{selector}' not found -- use 'python'/'typescript', or 'module.path:ClassName'"
        )
    module_name, class_name = selector.split(":", 1)
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as error:
        raise click.ClickException(f"Could not import plugin module '{module_name}': {error}") from error

    plugin_class = getattr(module, class_name, None)
    if not (isinstance(plugin_class, type) and issubclass(plugin_class, QueryCodegenPlugin)):
        raise click.ClickException(f"'{selector}' is not a QueryCodegenPlugin subclass")
    return plugin_class


@click.command(help="Generate typed code from a GraphQL query file")
@click.argument("query", nargs=-1, required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--schema", required=True, help="Python path to the schema, e.g. myapp.schema:schema")
@click.option(
    "-o",
    "--output-dir",
    required=True,
    type=click.Path(file_okay=False, writable=True, path_type=Path),
    help="Directory to write generated files into.",
)
@click.option(
    "-p",
    "--plugin",
    "plugins",
    multiple=True,
    required=True,
    help="Output plugin(s): 'python', 'typescript', or 'module.path:ClassName'. Repeatable.",
)
@click.option(
    "--app-dir",
    default=".",
    show_default=True,
    help="Look for the schema module in the specified directory, by adding it to the PYTHONPATH.",
)
def codegen(query: tuple[Path, ...], schema: str, output_dir: Path, plugins: tuple[str, ...], app_dir: str) -> None:
    schema_instance = load_schema(schema, app_dir=app_dir)
    plugin_classes = [_load_plugin(selector) for selector in plugins]
    output_dir.mkdir(parents=True, exist_ok=True)

    for query_path in query:
        query_text = query_path.read_text(encoding="utf-8")
        try:
            operation = generate_operation(schema_instance, query_text)
        except QueryCodegenError as error:
            raise click.ClickException(f"{query_path}: {error}") from error

        for plugin_class in plugin_classes:
            code = plugin_class().generate_code(operation)
            output_path = output_dir / f"{operation.name}.{plugin_class.file_extension}"
            output_path.write_text(code, encoding="utf-8")
            click.secho(f"Wrote {output_path}", fg="green")
