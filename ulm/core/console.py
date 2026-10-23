"""Lightweight ANSI console + logging helpers.

Replaces the old `rich`-based console. We keep a tiny hardcoded set of
colours and a log handler that writes plain text lines. Progress is a
single reusable carriage-return line so slow phones don't scroll-spam.
Every helper degrades gracefully when the stream is not a TTY.
"""
from __future__ import annotations

import logging
import os
import sys
import time
from typing import Optional, TextIO, Type

# --- ANSI helpers -------------------------------------------------------------------

_RESET = "\x1b[0m"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"
RED = "\x1b[31m"
GREEN = "\x1b[32m"
YELLOW = "\x1b[33m"
BLUE = "\x1b[34m"
MAGENTA = "\x1b[35m"
CYAN = "\x1b[36m"
GRAY = "\x1b[90m"


def colorize(text: str, *codes: str) -> str:
    """Wrap `text` in the given ANSI codes (no-op when not a TTY)."""
    if not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
        return text
    return "".join(codes) + text + _RESET


def supports_utf8() -> bool:
    encoding = getattr(sys.stdout, "encoding", "") or ""
    return "utf" in encoding.lower()


# --- Logger -------------------------------------------------------------------------


class ComfyHandler(logging.Handler):
    """Minimal log handler: `LEVEL message`, coloured level tag only."""

    _COLORS = {
        logging.DEBUG: GRAY,
        logging.INFO: "",
        logging.WARNING: YELLOW,
        logging.ERROR: RED,
        logging.CRITICAL: BOLD + RED,
    }
    _TAGS = {
        logging.DEBUG: "DBG",
        logging.INFO: "OK",
        logging.WARNING: "WRN",
        logging.ERROR: "ERR",
        logging.CRITICAL: "CRT",
    }

    def __init__(self, stream: Optional[TextIO] = None) -> None:
        super().__init__()
        self.stream = stream or sys.stderr

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = str(record.getMessage())
        except Exception:
            return
        if record.exc_info:
            import traceback

            message = message + "\n" + "".join(traceback.format_exception(*record.exc_info))
        code = self._COLORS.get(record.levelno, "")
        tag = self._TAGS.get(record.levelno, "LOG")
        prefix = f"{colorize(tag, code)} " if code else ""
        line = f"{prefix}{message}"
        try:
            self.stream.write(line + "\n")
            self.stream.flush()
        except Exception:
            pass


def setup_logging(debug: bool = False) -> None:
    """Install one root handler. Call once from the CLI entry."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler = ComfyHandler()
    formatter = logging.Formatter(fmt="%(message)s")
    handler.setFormatter(formatter)
    root.addHandler(handler)
    root.setLevel(logging.DEBUG if debug else logging.INFO)

    # Silence chatty stdlib/3rd-party modules even in debug mode.
    for noisy in ("urllib3", "urllib3.connectionpool", "requests", "m3u8"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


# --- Progress line ------------------------------------------------------------------


class ProgressLine:
    """A single reusable terminal line showing latest progress.

    Usage:
        p = ProgressLine("Segments")
        p.update(done=12, total=100, note="1.2 MiB/s")
        p.clear()
    """

    def __init__(self, label: str = "", stream: Optional[TextIO] = None, enabled: Optional[bool] = None) -> None:
        self.label = label
        self.stream = stream or sys.stdout
        self.enabled = (
            self.stream.isatty() and not os.environ.get("UNSHACKLE_QUIET")
            if enabled is None
            else enabled
        )
        self._started = 0.0
        self._written = False
        self._last_len = 0

    def update(
        self,
        done: int | float = 0,
        total: int | float = 0,
        note: str = "",
        pct: Optional[float] = None,
    ) -> None:
        if not self.enabled:
            return
        now = time.time()
        if now - self._started < 0.2:  # throttle redraws
            return
        self._started = now
        if pct is None:
            pct = 100.0 * float(done) / float(total) if total else 0.0
        bar_w = 24
        filled = int(round(bar_w * max(0.0, min(1.0, pct / 100.0))))
        bar = "#" * filled + "-" * (bar_w - filled)
        line = f"{self.label} [{bar}] {pct:5.1f}% {note}".rstrip()
        cr = "" if not self._written else "\r"
        self.stream.write(cr + line + " " * max(0, self._last_len - len(line)))
        self.stream.flush()
        self._written = True
        self._last_len = len(line)

    def clear(self) -> None:
        if self.enabled and self._written:
            self.stream.write("\r" + " " * self._last_len + "\r")
            self.stream.flush()
            self._written = False
            self._last_len = 0


def log(level: str, message: str) -> None:
    """Top-level convenience printer, e.g. log('info', 'starting')."""
    colors = {"info": "", "debug": GRAY, "warning": YELLOW, "error": RED, "success": GREEN}
    tag = {"info": "", "debug": "DBG", "warning": "WRN", "error": "ERR", "success": "OK"}
    if level not in colors:
        level = "info"
    prefix = f"{colorize(tag[level], colors[level])} " if tag[level] else ""
    sys.stderr.write(prefix + message + "\n")


class _Log:
    """logging-style facade: log.info('...'), convenience alias functions."""

    info = staticmethod(lambda m: log("info", m))
    success = staticmethod(lambda m: log("success", m))
    warning = staticmethod(lambda m: log("warning", m))
    error = staticmethod(lambda m: log("error", m))
    debug = staticmethod(lambda m: log("debug", m))


log = _Log()  # noqa: F811  -- rebind module-level name to the object API

__all__ = [
    "BOLD", "DIM", "RED", "GREEN", "YELLOW", "BLUE", "MAGENTA", "CYAN", "GRAY",
    "colorize", "supports_utf8", "setup_logging", "ProgressLine", "log",
]
