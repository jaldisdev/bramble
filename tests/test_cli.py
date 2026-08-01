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

from pathlib import Path

from click.testing import CliRunner

from bramble.cli.app import cli

# `bramble dev` isn't exercised here -- it launches a real uvicorn server (verified manually
# against a live process while building it; see project memory), which isn't a good fit for
# CliRunner's in-process invocation model. `bramble codegen` is a plain synchronous command that
# reads/writes files and returns, so it fits CliRunner directly.


def test_version_flag_reports_the_installed_package_version() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["--version"])

    assert result.exit_code == 0
    assert result.output.strip() == "bramble 0.1.0"

_SCHEMA_MODULE = """
import bramble


@bramble.type
class Author:
    name: str


@bramble.type
class Post:
    title: str
    author: Author


@bramble.type
class Query:
    @bramble.field
    def post_by_slug(slug: str) -> Post | None:
        return None


schema = bramble.Schema(query=Query, types=[Post, Author])
"""

_QUERY = """
query GetPost($slug: String!) {
  postBySlug(slug: $slug) {
    title
    author { name }
  }
}
"""


def test_codegen_command_writes_python_and_typescript_output(tmp_path: Path) -> None:
    (tmp_path / "schema_a.py").write_text(_SCHEMA_MODULE, encoding="utf-8")
    query_path = tmp_path / "get_post.graphql"
    query_path.write_text(_QUERY, encoding="utf-8")
    output_dir = tmp_path / "out"

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "codegen",
            str(query_path),
            "--schema",
            "schema_a:schema",
            "--app-dir",
            str(tmp_path),
            "-o",
            str(output_dir),
            "-p",
            "python",
            "-p",
            "typescript",
        ],
    )

    assert result.exit_code == 0, result.output
    python_output = (output_dir / "GetPost.py").read_text(encoding="utf-8")
    typescript_output = (output_dir / "GetPost.ts").read_text(encoding="utf-8")

    assert "class GetPostResult:" in python_output
    assert "postBySlug: GetPostResultPostBySlug | None" in python_output
    assert "export type GetPostResult = {" in typescript_output
    assert "postBySlug: GetPostResultPostBySlug | null;" in typescript_output


def test_codegen_command_reports_an_unknown_plugin(tmp_path: Path) -> None:
    (tmp_path / "schema_b.py").write_text(_SCHEMA_MODULE, encoding="utf-8")
    query_path = tmp_path / "get_post.graphql"
    query_path.write_text(_QUERY, encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "codegen",
            str(query_path),
            "--schema",
            "schema_b:schema",
            "--app-dir",
            str(tmp_path),
            "-o",
            str(tmp_path / "out"),
            "-p",
            "not-a-real-plugin",
        ],
    )

    assert result.exit_code != 0
    assert "not found" in result.output


def test_codegen_command_reports_a_bad_query_file(tmp_path: Path) -> None:
    (tmp_path / "schema_c.py").write_text(_SCHEMA_MODULE, encoding="utf-8")
    query_path = tmp_path / "bad.graphql"
    query_path.write_text('query GetPost { postBySlug(slug: "x") { doesNotExist } }', encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "codegen",
            str(query_path),
            "--schema",
            "schema_c:schema",
            "--app-dir",
            str(tmp_path),
            "-o",
            str(tmp_path / "out"),
            "-p",
            "python",
        ],
    )

    assert result.exit_code != 0
    assert "does not exist" in result.output


def test_codegen_command_reports_a_schema_that_is_not_a_bramble_schema(tmp_path: Path) -> None:
    (tmp_path / "schema_d.py").write_text("schema = object()\n", encoding="utf-8")
    query_path = tmp_path / "get_post.graphql"
    query_path.write_text(_QUERY, encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "codegen",
            str(query_path),
            "--schema",
            "schema_d:schema",
            "--app-dir",
            str(tmp_path),
            "-o",
            str(tmp_path / "out"),
            "-p",
            "python",
        ],
    )

    assert result.exit_code != 0
    assert "must resolve to an instance of bramble.Schema" in result.output
