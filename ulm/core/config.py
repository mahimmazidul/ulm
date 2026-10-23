"""Global configuration.

Reads unshackle.yaml from, in order: the path in $UNSHACKLE_CONFIG, the
current working directory (and nearby parents), and a per-user location.
Everything is loaded once into a module-level `config` object; nothing
mutates it at runtime (the old core mutated config during downloads).

All file paths accept `~` and absolute paths; optional per-directory
overrides in the yaml are relative to the data dir (see `data_dir`).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Optional, Union

import yaml

__all__ = ("Config", "config", "load_config", "save_config", "data_dir")


def data_dir() -> Path:
    """Directory where runtime data (cache, vaults, devices) lives."""
    if os.environ.get("UNSHACKLE_DATA"):
        return Path(os.environ["UNSHACKLE_DATA"]).expanduser()
    clone = Path(__file__).resolve().parents[2]  # repo dir containing unshackle/
    if (clone / "unshackle").is_dir():
        # Running from a clone: keep everything inside the repo dir.
        return clone
    home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "unshackle"
    return home.expanduser()


class Directories(NamedTuple):
    cache: Path
    cookies: Path
    logs: Path
    temp: Path
    wvds: Path
    prds: Path
    vaults: Path
    downloads: Path
    exports: Path


class Config:
    def __init__(self, data: Optional[Dict[str, Any]] = None):
        self._data: Dict[str, Any] = dict(data or {})

        raw_dirs: Dict[str, Any] = self._data.get("directories", {}) if isinstance(
            self._data.get("directories", {}), dict
        ) else {}
        base = data_dir()
        default_names = (
            "cache", "cookies", "logs", "temp", "wvds", "prds", "vaults", "downloads", "exports",
        )
        self.directories = Directories(
            **{name: _resolve(raw_dirs.get(name) or base / name) for name in default_names}
        )

        self.language = str(self._data.get("language", "en"))
        self.remote_cdm: List[Dict[str, Any]] = list(self._data.get("remote_cdm", []))
        self.key_vaults: List[Dict[str, Any]] = list(self._data.get("vaults", self._data.get("key_vaults", [])))
        self.headers: Dict[str, str] = {str(k): str(v) for k, v in (self._data.get("headers", {}) or {}).items()}
        self.dl: Dict[str, Any] = dict(self._data.get("dl", {}) or {})
        self.proxy: Dict[str, Any] = dict(self._data.get("proxy", {}) or {})
        self.cdm: Dict[str, Any] = dict(self._data.get("cdm", {}) or {})
        self.decryption: Union[str, Dict[str, Any]] = self._data.get("decryption", "widevine")
        self.services: Dict[str, Any] = dict(self._data.get("services", {}) or {})  # per-service overrides
        self.credentials: Dict[str, Any] = dict(self._data.get("credentials", {}) or {})
        self.temp_max_bytes: int = int(self._data.get("temp_max_bytes", 0))
        self.output_template: Dict[str, Any] = dict(self._data.get("output_template", {}) or {})
        self.folder_template: str = str(self._data.get("folder_template", ""))
        self.unicode_filenames: bool = bool(self._data.get("unicode_filenames", False))
        self.continue_downloads: bool = bool(self._data.get("continue_downloads", False))
        self.update_checks: bool = bool(self._data.get("update_checks", False))
        self.debug: bool = bool(self._data.get("debug", False))
        self.debug_keys: bool = bool(self._data.get("debug_keys", False))
        self.network: Dict[str, Any] = dict(self._data.get("network", {}) or {})

        self._loaded_path: Optional[Path] = None  # set by load_config()

    @property
    def config_dir(self) -> Optional[Path]:
        return self._loaded_path.parent if self._loaded_path else None

    def set(self, key: str, value: Any) -> None:
        """Write a top-level option into the underlying raw dict."""
        self._data[key] = value

    def unset(self, key: str) -> None:
        self._data.pop(key, None)

    def dump_to_data(self) -> Dict[str, Any]:
        return self._data

    def __bool__(self) -> bool:
        return bool(self._data)


def _resolve(value: Union[str, Path, os.PathLike]) -> Path:
    value = Path(str(value)).expanduser()
    return value if value.is_absolute() else data_dir() / value


def _config_candidates() -> List[Path]:
    candidates: List[Path] = []
    env_path = os.environ.get("UNSHACKLE_CONFIG")
    if env_path:
        candidates.append(Path(env_path).expanduser())
    cwd = Path.cwd()
    for base in (cwd, *list(cwd.parents)[:4]):
        candidates.append(base / "unshackle.yaml")
    clone = Path(__file__).resolve().parents[2]
    candidates.append(clone / "unshackle.yaml")
    candidates.append(Path.home() / "unshackle" / "unshackle.yaml")
    candidates.append(
        Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "unshackle" / "unshackle.yaml"
    )
    return candidates


def _config_path() -> Optional[Path]:
    for candidate in _config_candidates():
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    return None


def load_config() -> Config:
    path = _config_path()
    if path and path.is_file():
        try:
            with path.open("r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            if not isinstance(data, dict):
                data = {}
        except Exception as e:
            import ulm.core.console as console

            console.log.error(f"Failed to load config yaml {path}: {e}")
            data = {}
        cfg = Config(data)
        cfg._loaded_path = path
        return cfg
    return Config()


def save_config(cfg: Config) -> Path:
    """Write the config back to where it was loaded from (or a default spot)."""
    path = cfg._loaded_path or (data_dir() / "unshackle.yaml")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(cfg.dump_to_data(), f, allow_unicode=True, sort_keys=False)
    return path


config = load_config()
