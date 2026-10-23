"""`ulm wvd` — .wvd device management (pywidevine devices)."""
from __future__ import annotations

import shutil
from pathlib import Path

import click

from ulm.core.config import config
from ulm.core.console import log


@click.group("wvd", short_help="Manage Widevine .wvd device files")
def cli():
    pass


@cli.command("add")
@click.argument("device_path", type=click.Path(exists=True, dir_okay=False))
@click.option("--name", "renamed", default=None, help="Save the file under this name (without .wvd).")
def wvd_add(device_path: str, renamed: str = None):
    """Import a .wvd device file into the ulm device directory."""
    destination_dir = Path(config.directories.wvds)
    destination_dir.mkdir(parents=True, exist_ok=True)
    source = Path(device_path)
    name = renamed or source.stem
    destination = destination_dir / f"{name}.wvd"
    shutil.copyfile(source, destination)
    log.success(f"Copied {source.name} → {destination}")


@cli.command("list")
def wvd_list():
    """List imported .wvd device files."""
    devices = sorted(Path(config.directories.wvds).glob("*.wvd"))
    if not devices:
        log.info("No devices yet. Add one: ulm wvd add <path>")
    for device in devices:
        click.echo(f"• {device.stem}  ({device.name}, {device.stat().st_size} bytes)")


@cli.command("delete")
@click.argument("name")
def wvd_delete(name: str):
    """Delete an imported .wvd by name."""
    path = Path(config.directories.wvds) / f"{name}.wvd"
    if path.is_file():
        path.unlink()
        log.success(f"Deleted {path.name}")
    else:
        log.error(f"No device named {name}.wvd")
