"""`ulm env` — environment/layout introspection."""
from __future__ import annotations

import shutil

import click

from ulm.core.config import config, data_dir
from ulm.core.console import log


@click.group("env", short_help="Inspect the environment")
def cli():
    pass


@cli.command("check")
def env_check():
    """Check required binaries and layout."""
    missing = []
    for binary in ("ffmpeg", "mkvmerge", "mp4decrypt", "shaka-packager"):
        found = shutil.which(binary)
        state = "✔" if found else "✘"
        if not found:
            missing.append(binary)
        log.log("info", f"{state} {binary}" + (f" ({found})" if found else ""))
    if missing:
        log.warning(f"Missing (optional): {', '.join(missing)}")


@cli.command("info")
def env_info():
    """Show resolved data paths."""
    click.echo(f"data dir  : {data_dir()}")
    for name in ("cache", "cookies", "logs", "temp", "wvds", "prds", "vaults", "downloads", "exports"):
        click.echo(f"{name:<10}: {getattr(config.directories, name)}")
