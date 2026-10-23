"""CLI entry: `ulm ...` subcommand group (dl / wvd / kv / env / cfg)."""

from __future__ import annotations

import click

import ulm.commands as commands
from ulm.core.console import setup_logging


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.option("-d", "--debug", is_flag=True, default=False, help="Enable DEBUG-level logs.")
@click.option("-v", "--version", is_flag=True, default=False, help="Print version information.")
def main(debug: bool, version: bool):
    """ulm: Light on RAM. Fast to finish. Made to run anywhere."""
    setup_logging(debug=debug)
    if version:
        from ulm.core import __version__

        click.echo(f"ulm v{__version__}")


for _name in ("dl", "wvd", "kv", "env", "cfg"):
    _command = commands.load(_name)
    if callable(_command):
        main.add_command(_command)
