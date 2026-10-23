"""Service base class — the contract every service module implements.

Kept API-compatible with the old (Devine-lineage) contract so existing
service files keep working with minimal edits:

    class SERVICE(Service):
        def __init__(self, ctx_obj):          # gets the CLI context object
        def get_titles(self) -> Titles        # abstract
        def get_tracks(self, title)           # abstract
        def get_chapters(self, title)
        def get_widevine_license(...) / get_playready_license(...)
        def get_widevine_service_certificate(...)
        def get_clearkey_license(...)

What changed vs the old core:
- `get_session()` returns a small `requests` Session (use `.headers` and
  `.cookies` as before). TLS impersonation is opt-in (mount your own).
- `TrackRequest.codecs/ranges` moved to `ctx.obj.track_request`.
- No input-bridge/prompt plumbing; plain `click.prompt` in the CLI.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Callable, Dict, Generator, Optional, Tuple

import click

from ulm.core.config import config
from ulm.core.session import Session


class Service(ABC):
    """Service base class (see module docstring for the full contract)."""

    ALIASES: Tuple[str, ...] = ()
    GEOFENCE: Tuple[str, ...] = ()
    GEOBLOCK: Tuple[str, ...] = ()
    ANIME: bool = False
    DAILY: bool = False
    VAULT_TAG: Optional[str] = None
    PROXIES: Dict[str, str] = {}

    def __init__(self, ctx: Any = None, **kwargs: Any):
        self.ctx = ctx
        self.config_service = _service_config(self.__class__.TAG if hasattr(self.__class__, "TAG") else self.__class__.__name__)
        self.log = logging.getLogger(self.__class__.__name__)
        self.session: Session = self.get_session()
        if kwargs:
            for key, value in kwargs.items():
                setattr(self, key, value)

    @staticmethod
    def get_session() -> Session:
        """One shared Session type for every service (override freely)."""
        session = Session()
        session.headers.update(config.headers)
        return session

    @staticmethod
    def get_binaries() -> list:
        """Declare binaries this service needs (name list)."""
        return []

    @abstractmethod
    def get_titles(self) -> Any:
        """Return a Titles container (Movies / Series / Albums)."""

    @abstractmethod
    def get_tracks(self, title: Any) -> Any:
        """Return a Tracks container for a titled object."""

    def get_chapters(self, title: Any) -> Any:
        from ulm.core.tracks import Chapters

        return Chapters()

    # --- licensing hooks -------------------------------------------------

    def get_widevine_service_certificate(self, **kwargs: Any) -> Any:
        """Return the privacy-mode certificate (bytes / base64 str) or None."""
        return None

    def get_widevine_license(self, *, challenge: bytes, **kwargs: Any) -> Optional[Any]:
        raise NotImplementedError(f"{self.__class__.__name__} has no Widevine licensing")

    def get_playready_license(self, **kwargs: Any) -> Optional[Any]:
        """Default: PlayReady services implement this; fallback is None."""
        return None

    def get_clearkey_license(self, **kwargs: Any) -> Optional[Any]:
        return None


def _service_config(cls_tag: str) -> Dict[str, Any]:
    services = config.services or {}
    found = {}
    for key, value in services.items():
        if key.lower() == str(cls_tag).lower() and isinstance(value, dict):
            found = dict(value)
            break
    return found


class TrackRequest:
    """Requested codecs / ranges from the CLI (`-vcodec`, `--range`)."""

    def __init__(self, codecs: Optional[list] = None, ranges: Optional[list] = None, best_available: bool = False):
        self.codecs: list = codecs or []
        self.ranges: list = ranges or ["SDR"]
        self.best_available = bool(best_available)


__all__ = ("Service", "TrackRequest")
