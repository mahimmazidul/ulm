"""`ulm cfg` — get/set YAML persistence of runtime options."""
from __future__ import annotations

import click

from ulm.core.config import config, load_config, save_config
from ulm.core.console import log


@click.command("cfg", short_help="Read or write config values")
@click.argument("action")
@click.argument("key", required=False)
@click.argument("value", required=False)
def cli(action: str, key: str = None, value: str = None):
    if action == "get":
        if key is None:
            click.echo(config.dump_to_data())
            return
        data: dict = config.dump_to_data()
        result = data.get(key, None)
        if result is None and "." in key:
            found: dict = data
            for part in key.split("."):
                if not isinstance(found, dict):
                    break
                found = found.get(part, None)
                if found is None:
                    break
            result = found
        click.echo(result)
        return
    if action == "set":
        if key is None or value is None:
            raise click.ClickException("usage: ulm cfg set <key> <value>")
        from ulm.core.utils.yaml_lite import _scalar  # reuse the type coercion

        cfg = load_config() or config
        data = cfg.dump_to_data()
        parts = key.split(".")
        cursor: dict = data
        for part in parts[:-1]:
            node = cursor.get(part)
            if not isinstance(node, dict):
                node = {}
                cursor[part] = node
            cursor = node
        cursor[parts[-1]] = _scalar(value)
        cfg.set(key, None)
        cfg._data = data
        save_config(cfg)
        log.success(f"saved {key} = {value}")
        return
    if action == "unset":
        if key is None:
            raise click.ClickException("usage: ulm cfg unset <key>")
        cfg = load_config() or config
        cfg.unset(key)
        save_config(cfg)
        log.success(f"unset {key}")
        return
    raise click.ClickException("action must be one of: get, set, unset")
