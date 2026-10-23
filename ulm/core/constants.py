"""Constants and small shared types (no heavy imports)."""
from __future__ import annotations

from enum import Enum


class DRMSystem(str, Enum):
    WIDEVINE = "WIDEVINE"
    PLAYREADY = "PLAYREADY"
    CLEARKEY = "CLEARKEY"
    CLEARKEY_CENC = "CLEARKEY_CENC"
    MONALISA = "MONALISA"


class Descriptor(str, Enum):
    """Where a Track's URL list comes from."""

    SIMPLE = "SIMPLE"     # one or more direct URLs (download straight, no manifest)
    HLS = "HLS"
    DASH = "DASH"
    ISM = "ISM"


SYSTEM_NAMES = {
    DRMSystem.WIDEVINE: "Widevine",
    DRMSystem.PLAYREADY: "PlayReady",
    DRMSystem.CLEARKEY: "ClearKey",
    DRMSystem.CLEARKEY_CENC: "ClearKey-CENC",
    DRMSystem.MONALISA: "MonaLisa",
}

SYSTEM_CODES = {  # short lowercase tags users type with --drm / config `decryption:`
    DRMSystem.WIDEVINE: "widevine",
    DRMSystem.PLAYREADY: "playready",
    DRMSystem.CLEARKEY: "clearkey",
    DRMSystem.CLEARKEY_CENC: "clearkey-cenc",
    DRMSystem.MONALISA: "monalisa",
}


class DownloadCancelled(Exception):
    """Raised when a download was cancelled by the user (Ctrl+C) or API."""
