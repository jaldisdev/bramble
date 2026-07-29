from __future__ import annotations

import click

from bramble.cli.commands.codegen import codegen
from bramble.cli.commands.dev import dev


@click.group(name="bramble")
@click.version_option(package_name="bramble", message="%(prog)s %(version)s")
def cli() -> None:
    """bramble command-line tools."""


cli.add_command(dev)
cli.add_command(codegen)


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
