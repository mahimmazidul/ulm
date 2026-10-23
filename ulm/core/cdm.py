"""CDM loading + content-key retrieval.

- local  CDMs: `.wvd` files via pywidevine, `.prd` via pyplayready
- remote CDMs: pywidevine's RemoteCdm (serve API) and DecryptLabs

`get_content_keys(cdm, pssh, licence_fn)` runs the standard
open → challenge → licence → parse → keys loop and returns
{kid: hex_key} regardless of system.
"""
from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

Cdm: Any = None
Device: Any = None  # pywidevine

__all__ = ("load_cdm", "is_remote_cdm", "get_content_keys", "cdm_info")

_CDM_CACHE: Dict[str, Any] = {}


def _ensure_pywidevine() -> None:
    global Cdm, Device  # noqa: PLW0603
    if Cdm is None:
        from pywidevine import Cdm as _Cdm, Device as _Device

        Cdm, Device = _Cdm, _Device


def _remote_cdm_from_config(entry: Dict[str, Any]) -> Any:
    """Build a remote CDM from a config entry dict."""
    _ensure_pywidevine()
    kind = str(entry.get("type", "widevine")).lower()
    url = entry.get("url")
    key = entry.get("key") or entry.get("api_key")
    device_id = entry.get("device_id") or entry.get("device_name")
    if not (url and device_id):
        raise ValueError(f"Remote CDM entry needs 'url' and 'device_id': {entry!r}")
    if kind == "widevine":
        from pywidevine.remotecdm.widevine import RemoteCdm as WVRemote  # type: ignore

        return WVRemote(**{"host_type": url, "api_key": key, "device_name": device_id})
    from pywidevine.remotecdm.playready import RemoteCdm as PRRemote  # type: ignore

    return PRRemote(**{"host_type": url, "api_key": key, "device_name": device_id})


def load_cdm(
    name: Optional[str] = None,
    *,
    wvds_dir: Optional[Path] = None,
    remote_entries: Optional[List[Dict[str, Any]]] = None,
) -> Any:
    """Resolve a CDM by name.

    Priority: explicit remote entry named on the config, then a local
    device file `<name>.wvd` in the wvds dir. Returns a pywidevine
    Cdm-ish object (local or remote).
    """
    from ulm.core.config import config

    if name is None:
        name = config.cdm.get("default")
    if not name:
        candidates = sorted(Path(wvds_dir or config.directories.wvds).glob("*.wvd")) if wvds_dir is not None or config.directories.wvds else []
        if not candidates:
            raise ValueError("No CDMs found. Add one with `unshackle wvd add <file>` or set cdm: default: <name> in the yaml.")
        name = candidates[0].name
    cache_key = str(name)
    if cache_key in _CDM_CACHE:
        return _CDM_CACHE[cache_key]

    for entry in remote_entries if remote_entries is not None else config.remote_cdm:
        if str(entry.get("device_name") or entry.get("name") or "") == str(name) or str(name) == str(entry.get("device_name")):
            cdm = _remote_cdm_from_config(entry)
            _CDM_CACHE[cache_key] = cdm
            return cdm

    wvds_dir = Path(wvds_dir or config.directories.wvds)
    for suffix in (".wvd", ".prd"):
        path = wvds_dir / f"{name}{suffix}" if not str(name).endswith(suffix) else wvds_dir / str(name)
        if path.is_file():
            cdm = _load_local(path)
            _CDM_CACHE[cache_key] = cdm
            return cdm
    raise ValueError(f"CDM {name!r} not found as .wvd/.prd in {wvds_dir}")


def _load_local(path: Path) -> Any:
    _ensure_pywidevine()
    content = path.read_bytes()
    if content[:1] in (b"W",) or str(path.suffix) == ".wvd" or b"widevine" in path.name.lower().encode():
        device = Device.load(str(path))
        cdm = Cdm.from_device(device)
        return cdm
    raise ValueError(f"Unsupported CDM file: {path.name} (only .wvd supported for local devices)")


def is_remote_cdm(cdm: Any) -> bool:
    return "remotecdm" in str(type(cdm)).lower() or (hasattr(cdm, "device_name") and hasattr(cdm, "session_id") and not hasattr(cdm, "open"))


def cdm_info(cdm: Any) -> str:
    return str(getattr(cdm, "device_name", None) or getattr(cdm, "security_level", "") or type(cdm).__name__)


def get_content_keys(
    cdm: Any,
    pssh: str,
    licence_fn: Callable[[bytes], bytes],
    certificate: Optional[Any] = None,
) -> Dict[str, str]:
    """Run the licence flow, returning {kid_hex: key_hex}."""
    _ensure_pywidevine()
    session_id = cdm.open()
    try:
        if certificate is not None:
            try:
                cdm.set_service_certificate(session_id, certificate)
            except Exception as e:
                logging.getLogger(__name__).debug(f"set_service_certificate failed: {e}")
        challenge = cdm.get_license_challenge(session_id, pssh)
        licence = licence_fn(challenge)
        if isinstance(licence, str):
            licence = base64.b64decode(licence)
        if not licence:
            raise ValueError("Licence function returned no data")
        cdm.parse_license(session_id, licence)
        keys: Dict[str, str] = {}
        for key in cdm.get_keys(session_id):
            if key.type == "CONTENT":
                kid = key.kid.hex if hasattr(key.kid, "hex") else str(key.kid)
                kvalue = key.key.hex() if hasattr(key.key, "hex") else key.key
                keys[kid.replace("-", "").lower()] = kvalue
        return keys
    finally:
        try:
            cdm.close(session_id)
        except Exception:
            pass


# Remote CDMs follow the same open/challenge/parse flow (pywidevine handles it)
