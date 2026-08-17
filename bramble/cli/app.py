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

import click

from bramble.cli.commands.codegen import codegen
from bramble.cli.commands.dev import dev


@click.group(name="bramble")
# The distribution is `bramble-graphql` even though the command and import package are `bramble`.
# Recent click recovers from the mismatch by mapping the import package back to its distribution,
# but click 8.1 -- the floor this package allows -- raises instead, so name the distribution here.
@click.version_option(package_name="bramble-graphql", message="%(prog)s %(version)s")
def cli() -> None:
    """bramble command-line tools."""


cli.add_command(dev)
cli.add_command(codegen)


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
