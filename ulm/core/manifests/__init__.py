"""Manifest handlers: turn playlist/manifest URLs/bytes into Tracks.

Each subclass implements:
    matches(url) -> bool          # quick sniff by URL or extension
    from_url(url, session)        # fetch + parse
    create_tracks() -> Tracks
    download_track(track, session, prepare_drm, ...) -> Path(s)
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional, Type

from ulm.core.constants import Descriptor
from ulm.core.tracks import Tracks

__all__ = ("Manifest", "get_manifest_class")


class Manifest:
    descriptor: Descriptor = Descriptor.SIMPLE

    @classmethod
    def matches(cls, url: str) -> bool:
        raise NotImplementedError

    @classmethod
    def from_url(cls, url: str, session: Any) -> "Manifest":
        raise NotImplementedError

    def create_tracks(self) -> Tracks:
        raise NotImplementedError

    @classmethod
    def download_track(
        cls,
        track: Any,
        session: Any,
        prepare_drm: Optional[Callable[..., Any]] = None,
        progress: Optional[Callable[[Dict[str, Any]], Any]] = None,
        cancelled: Optional[Callable[[], bool]] = None,
        **kwargs: Any,
    ) -> Any:
        raise NotImplementedError


_CLS_CACHE: Dict[Descriptor, Type[Manifest]] = {}


def _load_classes() -> None:
    if _CLS_CACHE:  # already loaded
        return
    from ulm.core.manifests.hls import HLS
    from ulm.core.manifests.dash import DASH
    from ulm.core.manifests.ism import ISM

    _CLS_CACHE[Descriptor.HLS] = HLS
    _CLS_CACHE[Descriptor.DASH] = DASH
    _CLS_CACHE[Descriptor.ISM] = ISM


def get_manifest_class(descriptor: Descriptor) -> Optional[Type[Manifest]]:
    _load_classes()
    return _CLS_CACHE.get(descriptor)


def detect_descriptor(url: str) -> Optional[Descriptor]:
    """Best-effort sniff by file extension and common manifest params."""
    path = url.split("?")[0].lower()
    if path.endswith((".m3u8", ".m3u")) or "format=m3u8" in url.lower() or ".m3u8?" in url.lower():
        return Descriptor.HLS
    if path.endswith(".mpd"):
        return Descriptor.DASH
    if path.endswith(".ism") or path.endswith("/manifest") or path.endswith("/manifest()"):
        return Descriptor.ISM
    return None
