"""CLI subcommands. `load(name)` returns the click Command by simple name."""
from __future__ import annotations

from importlib import import_module
from typing import Any, Callable


def load(name: str) -> Callable[..., Any]:
    """Import ulm.commands.<name> and return its `cli` click Command."""
    module = import_module(f"ulm.commands.{name}")
    command = getattr(module, "cli", None) or getattr(module, name.replace("_", "_"), None)
    if command is None:
        raise ImportError(f"ulm.commands.{name} has no `cli`")
    return command


__all__ = ("load",)
