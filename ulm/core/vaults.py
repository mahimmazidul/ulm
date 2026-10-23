"""Key vault backends (SQLite by default; MySQL/HTTP allowed via plugins).

Vault interface: `get_key(kid, service)`, `add_key(kid, kid2, key, service)`,
`get_keys(kid)` (fan-out). All backends use stdlib sqlite3 (zero extra deps)
and one file per configured vault path.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from ulm.core.config import config

__all__ = ("Vault", "SQLiteVault", "Vaults", "load_vaults")


class Vault:
    """Abstract vault interface (services can implement their own)."""

    def __init__(self, name: str):
        self.name = name

    def get_key(self, kid: str, service: str) -> Optional[Dict[str, str]]:
        raise NotImplementedError

    def add_key(self, kid: str, key: str, service: str) -> bool:
        raise NotImplementedError

    def get_services(self) -> List[str]:
        raise NotImplementedError


def _vault_table(service: str) -> str:
    return "vault"  # single table; `service` column partitions rows safely


CREATE_SQL = """
CREATE TABLE IF NOT EXISTS vault (
    service TEXT NOT NULL,
    kid TEXT NOT NULL,
    kv TEXT NOT NULL,
    PRIMARY KEY (service, kid)
);
CREATE INDEX IF NOT EXISTS vault_kid ON vault (kid);
"""


class SQLiteVault(Vault):
    def __init__(self, path: Path):
        super().__init__(name=Path(path).name)
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()  # one connection per thread
        self._ensure_tables()

    def _conn(self) -> sqlite3.Connection:
        connection = getattr(self._local, "conn", None)
        if connection is not None:
            return connection
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        self._local.conn = connection
        return connection

    def _ensure_tables(self) -> None:
        with self._conn() as connection:
            connection.executescript(CREATE_SQL)
            connection.commit()

    def get_key(self, kid: str, service: str) -> Optional[Dict[str, str]]:
        try:
            row = self._conn().execute(
                "SELECT kid, kv FROM vault WHERE service = ? AND kid = ?",
                (service, kid.lower()),
            ).fetchone()
        except sqlite3.Error:
            self._ensure_tables()
            row = None
        if row:
            return {"kid": row["kid"], "key": row["kv"]}
        return None

    def add_key(self, kid: str, key: str, service: str) -> bool:
        table = _vault_table(service)
        try:
            cursor = self._conn().execute(
                f"INSERT OR IGNORE INTO vault (service, kid, kv) VALUES (?, ?, ?)",
                (service, kid.lower(), key),
            )
            self._conn().commit()
            return bool(cursor.rowcount)
        except sqlite3.Error as e:
            logging.getLogger(__name__).warning(f"vault write failed: {e}")
            try:
                self._ensure_tables()
            except sqlite3.Error:
                pass
            return False

    def get_keys(self, kid: str, services: Optional[List[str]] = None) -> Dict[str, "Vault"]:
        """Query every service table in one shot (kid-first lookup)."""
        keys: Dict[str, "Vault"] = {}
        for service in services or self.get_services():
            found = self.get_key(kid, service)
            if found:
                keys[service] = self
                break
        return keys

    def get_services(self) -> List[str]:
        rows = self._conn().execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        return [row[0] for row in rows if not row[0].startswith("sqlite_")]

    def get_all_keys(self, service: str) -> Dict[str, str]:
        try:
            return {row["kid"]: row["kv"] for row in self._conn().execute(f"SELECT kid, kv FROM {_vault_table(service)}")}
        except sqlite3.Error:
            return {}


class Vaults:
    """Fan-out across every configured vault; first hit wins."""

    def __init__(self, vaults: Optional[List[Vault]] = None):
        self.vaults: List[Vault] = vaults or []

    def find_key(self, kid: str, service: str) -> Optional[Dict[str, str]]:
        for vault in self.vaults:
            found = vault.get_key(kid, service)
            if found:
                return {"kid": found["kid"], "key": found["key"], "vault": vault.name}
        return None

    def add_key(self, kid: str, key: str, service: str) -> List[str]:
        wrote: List[str] = []
        for vault in self.vaults:
            try:
                if vault.add_key(kid, key, service):
                    wrote.append(vault.name)
            except Exception as e:
                logging.getLogger(__name__).warning(f"vault {vault.name} add failed: {e}")
        return wrote

    def __len__(self) -> int:
        return len(self.vaults)

    def __bool__(self) -> bool:
        return bool(self.vaults)


def load_vaults() -> Vaults:
    """Build `Vaults` from the yaml `vaults:` list (sqlite: path/name.db)."""
    entries: List[Vault] = []
    base: Any = getattr(config, "directories", None) and config.directories.vaults
    for vault_config in config.key_vaults:
        if not isinstance(vault_config, dict):
            continue
        kind = str(vault_config.get("type", "sqlite")).lower()
        if kind in ("sqlite", "sqlite3"):
            path = vault_config.get("path", "vault.db")
            entries.append(SQLiteVault(Path(str(path)).expanduser()))
        elif kind in ("mysql", "http", "api"):
            logging.getLogger(__name__).warning(
                f"Vault kind '{kind}' is not bundled with the lite core; add it via plugins."
            )
    if not entries and base:
        default_path = Path(str(base)) / "vault.db"
        if default_path.exists():
            entries.append(SQLiteVault(default_path))
    return Vaults(entries)
