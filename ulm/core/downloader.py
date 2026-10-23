"""Segmented, resumable, RAM-bounded downloader.

Design goals:
- stream everything (we never hold a whole file or more than `chunk`
  bytes plus one pending segment in memory)
- resumable per segment: a sidecar JSON remembers which segment
  indexes finished, so a re-run skips them
- small thread pool, default 4 workers; `-w`/`--workers` can raise it
- yields progress dicts for a caller-provided progress callback

API:
    download_url(session, url, save_path, headers=..., on_progress=...)
    download_segments(session, urls, output_dir, ..., on_progress=...)
"""
from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

import requests

from ulm.core.constants import DownloadCancelled
from ulm.core.session import DEFAULT_TIMEOUT

__all__ = ("download_url", "download_segments", "set_speed_limit", "DownloadError")

ProgressCallback = Callable[[Dict[str, Any]], None]
TempExtension = ".!ush"  # hidden-ish, short: important on slow Android storage

CHUNK = 1 << 17  # 128 KiB read chunk: small enough for several workers on 1-2GB RAM
SIDE_NAME = ".ush-manifest"


class DownloadError(Exception):
    pass


class _TokenBucket:
    """Optional shared rate limiter (bytes/sec). 0/disabled by default."""

    def __init__(self) -> None:
        self.rate = 0.0
        self._tokens = 0.0
        self._last = time.monotonic()

    def consume(self, amount: int) -> None:
        if self.rate <= 0:
            return
        now = time.monotonic()
        self._tokens = min(self.rate, self._tokens + (now - self._last) * self.rate)
        self._last = now
        need = float(amount)
        while need > 0:
            if self._tokens >= need:
                self._tokens -= need
                need = 0
            else:
                need -= self._tokens
                self._tokens = 0
                time.sleep(max(0.01, need / self.rate))


_bucket = _TokenBucket()


def set_speed_limit(bytes_per_second: float) -> None:
    """Set the shared download speed limit (0 disables)."""
    _bucket.rate = max(0.0, float(bytes_per_second))


def download_url(
    session: requests.Session,
    url: str,
    save_path: Path,
    headers: Optional[Dict[str, str]] = None,
    cookies: Optional[Any] = None,
    on_progress: Optional[ProgressCallback] = None,
    cancelled: Optional[Callable[[], bool]] = None,
    tmp_suffix: str = TempExtension,
) -> Path:
    """Download one URL to `save_path` (streaming, resumable for ranged servers).

    Resume is byte-accurate only when the server supports Range requests;
    otherwise the partial file restarts from scratch.
    Returns the final Path.
    """
    save_path = Path(save_path)
    save_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = save_path.with_name(save_path.name + tmp_suffix)
    req_headers = {"Accept-Encoding": "identity"}  # we must know the true byte length
    if headers:
        req_headers.update(headers)

    done = tmp_path.stat().st_size if tmp_path.exists() else 0
    if done and not _server_supports_range(session, url, req_headers, cookies):
        done = 0
        tmp_path.unlink(missing_ok=True)

    range_headers = dict(req_headers)
    if done:
        range_headers["Range"] = f"bytes={done}-"

    try:
        response = session.get(url, headers=range_headers, cookies=cookies, stream=True, max_retries=0)
    except requests.RequestException as e:
        raise DownloadError(f"GET {url} failed: {e}") from e

    if response.status_code == 416:  # range not satisfiable: already complete?
        if _content_length_finishes(done, response):
            tmp_path.rename(save_path)
            return save_path
        raise DownloadError(f"HTTP 416 for {url} at offset {done}")

    if done and response.status_code != 206:
        # Server ignored Range; restart from 0.
        done = 0
        response.close()
        response = session.get(url, headers=req_headers, cookies=cookies, stream=True, max_retries=0)

    if response.status_code == 416:
        response.close()
        raise DownloadError(f"Range not satisfiable for {url} at {done}")
    if response.status_code >= 400:
        response.close()
        raise DownloadError(f"HTTP {response.status_code} for {url}")

    total = int(response.headers.get("Content-Length", "0") or 0)
    if response.status_code == 206:
        total = _parse_total_from_content_range(response.headers.get("Content-Range", ""), total) or total
    if total and total <= done:
        response.close()
        tmp_path.rename(save_path)
        if on_progress:
            on_progress({"downloaded": total, "total": total})
        return save_path

    write_pos = done
    try:
        with tmp_path.open("r+b" if done else "wb") as f:
            if done:
                f.seek(done)
            for chunk in response.iter_content(chunk_size=CHUNK):
                if cancelled and cancelled():
                    raise DownloadCancelled()
                if chunk:
                    _bucket.consume(len(chunk))
                    f.write(chunk)
                    write_pos += len(chunk)
                    if on_progress:
                        on_progress({"downloaded": write_pos, "total": total or write_pos})
    except DownloadCancelled:
        raise
    except OSError as e:
        raise DownloadError(f"I/O error writing {tmp_path}: {e}") from e
    finally:
        try:
            response.close()
        except Exception:
            pass

    if total and write_pos < total:
        raise DownloadError(f"Truncated download for {url}: {write_pos}/{total}")
    tmp_path.rename(save_path)  # atomic-ish completion on POSIX
    return save_path


def _server_supports_range(
    session: requests.Session, url: str, headers: Dict[str, str], cookies: Optional[Any]
) -> bool:
    """HEAD (or a tiny GET) the server; True when it answers Range properly."""
    try:
        probe = dict(headers)
        probe["Range"] = "bytes=0-0"
        response = session.get(url, headers=probe, cookies=cookies, stream=True, max_retries=0)
    except requests.RequestException:
        return False
    with response:
        return response.status_code == 206


def _parse_total_from_content_range(content_range: str, fallback: int) -> int:
    """`bytes 0-1048575/10485760` -> 10485760; falls back when unparseable."""
    try:
        return int(content_range.rsplit("/", 1)[1]) or fallback
    except (ValueError, IndexError):
        return fallback


def _content_length_finishes(current: int, response: requests.Response) -> bool:
    total = int(response.headers.get("Content-Length", "0") or 0)
    return bool(total) and current >= total


def download_segments(
    session: requests.Session,
    urls: Sequence[str],
    output_dir: Path,
    filename_template: str = "{index:08d}",
    headers: Optional[Dict[str, str]] = None,
    cookies: Optional[Any] = None,
    max_workers: int = 4,
    on_progress: Optional[ProgressCallback] = None,
    cancelled: Optional[Callable[[], bool]] = None,
    extension: str = ".mp4",
    allow_resume: bool = True,
) -> List[Path]:
    """Download many segment URLs into `output_dir/<template>` files.

    A `.ush-manifest` sidecar lists finished segment indexes; when
    `allow_resume` is on, finished segments are skipped on a re-run.

    Returns the ordered list of Paths. Raises DownloadCancelled when the
    `cancelled()` callback turns truthy (Ctrl+C / external stop).
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    sidecar = output_dir / SIDE_NAME
    finished: set[int] = set()
    if allow_resume and sidecar.exists():
        try:
            finished = {int(i) for i in json.loads(sidecar.read_text(encoding="utf-8"))}
        except (ValueError, OSError):
            finished = set()

    pending = [(i, url) for i, url in enumerate(urls) if i not in finished]
    paths: Dict[int, Path] = {
        i: output_dir / f"{filename_template.format(index=i)}{extension}" for i in range(len(urls))
    }
    for i in finished:
        if not paths[i].exists():
            finished.discard(i)
            pending.append((i, urls[i]))

    total = len(urls)
    count_done = len(finished)
    if on_progress:
        on_progress({"segments_done": count_done, "segments_total": total})

    if pending:
        errors: List[str] = []
        workers = max(1, min(int(max_workers), len(pending)))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="ush-dl") as pool:
            futures_to_index = {}
            for index, url in pending:
                if cancelled and cancelled():
                    raise DownloadCancelled()
                futures_to_index[pool.submit(
                    _download_one, session, index, url, paths[index], headers, cookies, cancelled
                )] = index
            for future in as_completed(futures_to_index):
                if cancelled and cancelled():
                    raise DownloadCancelled()
                index = futures_to_index[future]
                try:
                    future.result()
                except DownloadCancelled:
                    raise
                except Exception as e:
                    errors.append(f"segment {index}: {e}")
                    continue
                finished.add(index)
                count_done += 1
                if on_progress:
                    on_progress({"segments_done": count_done, "segments_total": total})
                if allow_resume:
                    _write_sidecar(sidecar, finished)
        if errors:
            raise DownloadError(f"{len(errors)} segment(s) failed: {errors[:5]}")

    if allow_resume:
        _write_sidecar(sidecar, finished)
        try:
            sidecar.unlink()
        except OSError:
            pass
    return [paths[i] for i in range(len(urls))]


def _download_one(
    session: requests.Session,
    index: int,
    url: str,
    path: Path,
    headers: Optional[Dict[str, str]],
    cookies: Optional[Any],
    cancelled: Optional[Callable[[], bool]],
) -> Path:
    if path.exists():
        return path  # previous run finished this segment
    return download_url(session, url, path, headers=headers, cookies=cookies, cancelled=cancelled)


def _write_sidecar(sidecar: Path, finished: Iterable[int]) -> None:
    try:
        sidecar.write_text(json.dumps(sorted(finished)), encoding="utf-8")
    except OSError:
        pass


def sweep_temp_dirs(temp_dir: Path, max_bytes: int = 0) -> None:
    """Delete stale `.!ush` temp files; if `max_bytes` is set, trim the
    oldest files first (they are only scratch space)."""
    temp_dir = Path(temp_dir)
    if not temp_dir.is_dir():
        return
    candidates = [p for p in temp_dir.rglob("*") if p.is_file() and (p.name.endswith(TempExtension) or p.name == SIDE_NAME)]
    for p in candidates:
        try:
            p.unlink()
        except OSError:
            pass
    if max_bytes > 0:
        entries = [p for p in temp_dir.rglob("*") if p.is_file()]
        total = sum(p.stat().st_size for p in entries)
        if total <= max_bytes:
            return
        for p in sorted(entries, key=lambda q: q.stat().st_mtime):
            if total <= max_bytes:
                break
            size = p.stat().st_size
            try:
                p.unlink()
                total -= size
            except OSError:
                continue
