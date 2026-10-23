"""Service discovery: `services/<TAG>/__init__.py` packages.

A service is a directory with an `__init__.py` exporting either a class
named after the directory (e.g. `NF`), or a click Command exposing the
class inside it. Tags are case-insensitive for CLI lookup.
"""
from __future__ import annotations

import importlib
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import click

from ulm.core.service import Service

__all__ = ("Services", "load_service", "load_all_services", "service_tags")

_SERVICE_CACHE: Dict[str, Any] = {}


def services_dir(extra: Optional[List[str]] = None) -> List[Path]:
    candidates: List[Path] = []
    from ulm.core.config import config, data_dir

    clone = Path(__file__).resolve().parents[3] / "services"
    if clone.is_dir():
        candidates.append(clone)
    data = data_dir() / "services"
    if data.is_dir():
        candidates.append(data)
    return candidates


def service_tags() -> List[str]:
    tags: List[str] = []
    for directory in services_dir():
        for entry in sorted(directory.iterdir()):
            if entry.is_dir() and (entry / "__init__.py").is_file():
                tags.append(entry.name)
    return sorted(set(tags), key=str.upper)


def load_service(tag: str) -> Any:
    """Import the service module and return its Service class (cached)."""
    key = tag.lower()
    if key in _SERVICE_CACHE:
        return _SERVICE_CACHE[key]
    last_error: Optional[BaseException] = None
    for directory in services_dir():
        path = directory / tag if (directory / tag).is_dir() else None
        if path is None:
            for entry in directory.iterdir():
                if entry.is_dir() and entry.name.lower() == key:
                    path = entry
                    break
        if path is None or not (path / "__init__.py").is_file():
            continue
        sys.path.insert(0, str(directory))
        try:
            module = importlib.import_module(tag)
            service_class = _extract_service_class(module, tag)
            if service_class:
                _SERVICE_CACHE[key] = service_class
                return service_class
        except Exception as e:  # surface real module/config errors to the user
            last_error = e
            logging.getLogger(__name__).debug("loading %s failed:", tag, exc_info=True)
        finally:
            try:
                sys.path.remove(str(directory))
            except ValueError:
                pass
    if last_error is not None:
        raise click.ClickException(f"Loading service {tag!r} failed: {last_error}") from last_error
    raise ServiceNotFound(tag)


class ServiceNotFound(click.ClickException):
    def __init__(self, tag: str):
        super().__init__(
            f"Service {tag!r} was not found. Place it in services/<TAG>/__init__.py or check the tag spelling."
        )


def _extract_service_class(module: Any, tag: str) -> Optional[type]:
    candidates = [getattr(module, tag, None), getattr(module, "SERVICE", None), getattr(module, "cli", None)]
    for candidate in candidates:
        if isinstance(candidate, type) and issubclass(candidate, Service) and candidate is not Service:
            return candidate
        if candidate is not None and hasattr(candidate, "service"):  # click command wrapping a hidden class
            helper = getattr(candidate, "service")
            if isinstance(helper, type) and issubclass(helper, Service):
                return helper
    return None


def load_all_services() -> Dict[str, Any]:
    loaded: Dict[str, Any] = {}
    from ulm.core.config import config

    only = config.services.get("enabled") if isinstance(config.services, dict) else None
    for tag in service_tags():
        if isinstance(only, list) and only and str(tag).lower() not in [str(x).lower() for x in only]:
            continue
        try:
            loaded[tag] = load_service(tag)
        except Exception as e:
            logging.getLogger(__name__).warning(f"Cannot load service {tag}: {e}")
    return loaded


class Services:
    """click.Group-compatible helper listing available service subcommands."""

    def __init__(self) -> None:
        self._tags = service_tags()

    def list(self) -> List[str]:
        return list(self._tags)
