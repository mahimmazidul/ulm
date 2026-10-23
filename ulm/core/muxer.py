"""Muxing + renaming: mkvmerge first, ffmpeg fallback. Zero deps.

The pipeline calls `mux(tracks, title, output)` with downloaded/decrypted
Track objects; we assemble an externals-safe mkv (or mp4) file, then the
caller renames via `Title.get_filename`.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, List, Optional

__all__ = ("mux", "find_binary", "BinaryNotFound")

log = logging.getLogger(__name__)

_BIN_CACHE: dict = {}


def find_binary(name: str) -> Optional[str]:
    """Locate a binary on PATH (cached)."""
    if name in _BIN_CACHE:
        return _BIN_CACHE[name]
    location = shutil.which(name)
    if location:
        _BIN_CACHE[name] = location
    return location


def BinaryNotFound(*names: str) -> FileNotFoundError:  # noqa: N802 — factory, lowercase would be confusing
    return FileNotFoundError(f"None of {', '.join(names)} found on PATH; install it (or set PATH)")


def mux(
    tracks: Any,
    title: Any,
    output: Path,
    *,
    chapter_name: str = "",
    mkv_toolnix = True,
    progress: Any = None,
) -> Path:
    """Mux Videos+Audio+Subtitles into an MKV (or MP4 when no mkvmerge).

    `tracks`: a Tracks container with tracks whose `.path` are set (already
    downloaded/decrypted). `title`: a Title for chapters/naming.
    """
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    video_tracks = tracks.videos or []
    audio_tracks = tracks.audio
    sub_tracks = tracks.subtitles
    chapters = tracks.chapters

    mkvmerge = find_binary("mkvmerge")
    if mkvmerge:
        return _mux_mkvmerge(
            mkvmerge,
            video_tracks,
            audio_tracks,
            sub_tracks,
            chapters,
            output,
            progress=progress,
        )
    ffmpeg = find_binary("ffmpeg")
    if ffmpeg:
        return _mux_ffmpeg(ffmpeg, video_tracks, audio_tracks, sub_tracks, chapters, output, progress=progress)
    raise BinaryNotFound("mkvmerge", "ffmpeg")


def _mux_mkvmerge(
    mkvmerge: str,
    video_tracks: List[Any],
    audio_tracks: List[Any],
    sub_tracks: List[Any],
    chapters: Any,
    output: Path,
    progress: Any = None,
) -> Path:
    """mkvmerge: explicit order and language tags."""
    command = ["mkvmerge", "-o", str(output)]
    chapter_file: Optional[str] = None
    if chapters and len(chapters) > 0:
        import tempfile

        tmp = Path(tempfile.mkstemp(prefix="chapters_", suffix=".txt")[1])
        chapters.write_ogm(tmp)
        chapter_file = str(tmp)
        command += ["--chapters", chapter_file]

    language_map = [(video_tracks, "v"), (audio_tracks, "a"), (sub_tracks, "s")]
    for bucket, kind in language_map:
        for track in bucket:
            if not track.path:
                continue
            code = track.language or "und"
            command += [f"--language", f"0:{code}"]
            command.append(str(track.path))

    process = subprocess.run(command, capture_output=True)
    try:
        if process.returncode not in (0, 1):  # 1 = warnings ok for mkvmerge
            raise RuntimeError(f"mkvmerge failed: {process.stderr.decode('utf-8', 'replace')[-2000:]}")
    finally:
        if chapter_file:
            try:
                Path(chapter_file).unlink(missing_ok=True)
            except OSError:
                pass
    return output


def _mux_ffmpeg(
    ffmpeg: str,
    video_tracks: List[Any],
    audio_tracks: List[Any],
    sub_tracks: List[Any],
    chapters: Any,
    output: Path,
    progress: Any = None,
) -> Path:
    inputs: List[str] = []
    maps: List[str] = []
    metadata: List[str] = []
    index = 0

    for track in video_tracks:
        if not track.path:
            continue
        inputs += ["-i", str(track.path)]
        maps += ["-map", f"{index}:0"]
        metadata += [f"-metadata:s:v:{index}", f"language={track.language or 'und'}"]
        index += 1
    for track in audio_tracks:
        if not track.path:
            continue
        inputs += ["-i", str(track.path)]
        maps += ["-map", f"{index}:0"]
        metadata += [f"-metadata:s:a:{index}", f"language={track.language or 'und'}"]
        index += 1
    for track in sub_tracks:
        if not track.path:
            continue
        inputs += ["-i", str(track.path)]
        maps += ["-map", f"{index}:0"]
        metadata += [f"-metadata:s:s:{index}", f"language={track.language or 'und'}"]
        index += 1

    if not maps:
        raise RuntimeError("No tracks to mux")
    command = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]
    if chapters is not None and len(chapters) > 0:
        import tempfile

        tmp = Path(tempfile.mkstemp(prefix="ffmeta_", suffix=".txt")[1])
        lines = [";FFMETADATA1"]
        for chapter in chapters:
            start_ms = int(chapter.timestamp * 1000)
            lines.append(f"[CHAPTER]\nTIMEBASE=1/1000\nSTART={start_ms}\nEND={start_ms + 1000}\ntitle={chapter.name}")
        tmp.write_text("\n".join(lines), encoding="utf-8")
        command += ["-f", "ffmetadata", "-i", str(tmp)]
    command += inputs + maps + ["-c", "copy", str(output)]
    process = subprocess.run(command, capture_output=True)
    if process.returncode != 0:
        raise RuntimeError(f"ffmpeg mux failed: {process.stderr.decode('utf-8', 'replace')[-2000:]}")
    return output


def decrypt_track_for_mux(input_path: Path, kid: str, key: str, output: Optional[Path] = None) -> Path:
    """Decrypt one track file's fragments with mp4decrypt/shaka-packager."""
    input_path = Path(input_path)
    output = output or input_path.with_name(input_path.stem + "_dec" + input_path.suffix)
    for binary, args in (
        ("mp4decrypt", lambda b: [b, f"--key", f"{kid}:{key}", str(input_path), str(output)]),
        ("shaka-packager", lambda b: [b, f"--decryption_key", f"{kid}:{key}", f"input={input_path},stream=output", "null"]),
    ):
        location = find_binary(binary)
        if location:
            command = args(location)
            process = subprocess.run(command, capture_output=True)
            if process.returncode != 0:
                continue
            if output.exists():
                return output
    raise BinaryNotFound("mp4decrypt", "shaka-packager")
