"""Tiny JSON serialization helpers shared by tracks / export / CLI."""
from __future__ import annotations

from typing import Any, Dict

from ulm.core.constants import DRMSystem
from ulm.core.tracks import DRMInfo


def track_to_dict(track: Any) -> Dict[str, Any]:
    """Serialize a Track (Video/Audio/Subtitle) to a plain dict."""
    data: Dict[str, Any] = {
        "id": track.id,
        "url": track.url,
        "urls": list(track.urls),
        "descriptor": track.descriptor.value if hasattr(track.descriptor, "value") else str(track.descriptor),
        "language": str(track.language) if track.language else None,
        "is_original_lang": bool(track.is_original_lang),
        "kid": track.kid,
        "data": {k: (v if isinstance(v, (str, int, float, bool, list, type(None))) else str(v)) for k, v in (track.data or {}).items()},
        "drm": [
            {
                "system": d.system.value if hasattr(d.system, "value") else str(d.system),
                "pssh": (d.pssh if isinstance(d.pssh, str) else getattr(d.pssh, "decode", lambda *a: None)("latin-1") if d.pssh else None),
                "kid": d.kid,
                "keys": dict(getattr(d, "keys", {}) or {}),
            }
            for d in track.drm
        ],
    }
    for attr in ("height", "width", "range", "fps", "codec", "bitrate", "channels", "subtitle_codec", "forced", "sdh"):
        if hasattr(track, attr):
            value = getattr(track, attr)
            data[attr] = value
    return data


def drm_info_from_dict(item: Dict[str, Any]) -> DRMInfo:
    return DRMInfo(
        DRMSystem(item["system"]),
        item.get("pssh"),
        kid=item.get("kid"),
    )
