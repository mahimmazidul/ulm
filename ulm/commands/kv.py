"""`ulm kv` — key vault helpers."""
from __future__ import annotations

import click

from ulm.core.config import config
from ulm.core.console import log
from ulm.core.vaults import load_vaults


@click.group("kv", short_help="Inspect/add content keys")
def cli():
    pass


@cli.command("add")
@click.argument("service")
@click.argument("kid")
@click.argument("key")
@click.option("--vault", "vault_name", default=None, help="Write to the named vault only.")
def kv_add(service: str, kid: str, key: str, vault_name: str = None):
    """Add a CONTENT key (kid, hex key) to a vault under a service tag."""
    manager = load_vaults()
    if not manager:
        log.error("No vaults configured (vaults: in unshackle.yaml)")
        return
    wrote = manager.add_key(kid, key, service)
    log.success(f"wrote to: {', '.join(wrote) if wrote else 'none'}")


@cli.command("find")
@click.argument("service")
@click.argument("kid")
def kv_find(service: str, kid: str):
    """Query the vault for a KID under a service tag."""
    manager = load_vaults()
    found = manager.find_key(kid, service) if manager else None
    if found:
        click.echo(f"{found['kid']}:{found['key']}  (vault: {found['vault']})")
    else:
        log.error("not found")


@cli.command("list")
def kv_list():
    """List configured vaults."""
    manager = load_vaults()
    if not manager:
        log.info("No vaults configured.")
        return
    for vault in manager.vaults:
        click.echo(f"• {vault.name} ({type(vault).__name__})")
