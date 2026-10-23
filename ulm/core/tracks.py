"""Track classes.

Kept API-compatible with the old core where it matters for services:
`Track(id, url, descriptor, language, is_original_lang, drm, data, kid)`,
`Video(height, range, fps, codec, ...)`, `Audio(channels, bitrate)`,
`Subtitle`, `Chapter`, `Chapters`, and the `Tracks` container with
`.videos/.audio/.subtitles/.chapters`, `sorted_*` selectors, and
`.select_*()` helpers.
"""
from __future__ import annotations

import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Union

from ulm.core.constants import Descriptor, DRMSystem
from ulm.core.utils.language import Language

# MP4 box inspection with zero extra deps ---------------------------------

_MOOV = b"moov"
_MOOF = b"moof"
_SENC = b"senc"


def top_level_boxes(data: bytes) -> Iterator[tuple[bytes, int, int]]:
    """Yield (box_type, offset, size) for every top-level MP4 box."""
    offset = 0
    end = len(data)
    while offset + 8 <= end:
        size = int.from_bytes(data[offset:offset + 4], "big")
        box_type = data[offset + 4:offset + 8]
        if size == 0:
            size = end - offset
        elif size == 1:
            if offset + 16 > end:
                break
            size = int.from_bytes(data[offset + 8:offset + 16], "big")
        if size < 8 or offset + size > end:
            break
        yield box_type, offset, size
        offset += size


def box_find(data: bytes, target: bytes) -> Optional[bytes]:
    """Return the raw bytes of the FIRST top-level `target` box, or None."""
    for box_type, offset, size in top_level_boxes(data):
        if box_type == target:
            return data[offset:offset + size]
    return None


def moof_still_encrypted(data: bytes) -> bool:
    """True when a 'moof' fragment carries a 'senc' box (un-decrypted CENC)."""
    for box_type, offset, size in top_level_boxes(data):
        if box_type != _MOOF:
            continue
        fragment = data[offset:offset + size]
        pos = 0
        while pos + 8 <= len(fragment):
            f_size = int.from_bytes(fragment[pos:pos + 4], "big")
            f_type = fragment[pos + 4:pos + 8]
            if f_type == _SENC:
                return True
            if f_size >= 8:
                pos += f_size
            else:
                break
    return False


class Track:
    """Base class of every downloadable asset the manifests produce."""

    def __init__(
        self,
        *,
        id_: str = "",
        url: Optional[str] = None,
        urls: Optional[Sequence[str]] = None,
        descriptor: Descriptor = Descriptor.SIMPLE,
        language: Any = None,
        is_original_lang: bool = False,
        data: Optional[Dict[str, Any]] = None,
        drm: Optional[Sequence["DRMInfo"]] = None,
    ):
        self.id = str(id_ or os.urandom(4).hex())
        self.url = url or ""
        self.urls: List[str] = list(urls or ([url] if url else []))
        self.descriptor = Descriptor(descriptor)
        self.language = language  # a `language.Language` tag or None
        self.is_original_lang = bool(is_original_lang)
        self.data: Dict[str, Any] = dict(data or {})  # service/manifest extra info
        self.drm: List["DRMInfo"] = list(drm or [])
        self.kid: Optional[str] = None  # 32-char hex key id if known up front
        self.path: Optional[Path] = None  # set once downloaded
        self.size: Optional[int] = None
        self.needs_repack = False
        self.context: Dict[str, Any] = {}

    @property
    def type(self) -> str:
        return self.__class__.__name__.lower()

    def get_key_id(self, session: Any) -> Optional[str]:
        """Best-effort KID from the init segment data (no downloads by callers)."""
        if self.kid:
            return self.kid
        init_url = self.data.get("init_segment_url")
        if not init_url:
            return None
        try:
            response = session.get(init_url, max_retries=1)
            init = response.content
        except Exception:
            return None
        kid = find_kid_in_init(init)
        if kid:
            self.kid = kid
        return kid

    def get_init_segment(self, session: Any) -> Optional[bytes]:
        """Return the init segment bytes if this track has one."""
        init_url = self.data.get("init_segment_url")
        if not init_url:
            return None
        try:
            return session.get(init_url, max_retries=1).content
        except Exception:
            return None

    def downloaded(self) -> bool:
        return self.path is not None and Path(self.path).exists()

    def move(self, new_path: Union[str, Path]) -> None:
        new_path = Path(new_path)
        if self.path:
            new_path.parent.mkdir(parents=True, exist_ok=True)
            os.replace(self.path, new_path)
            self.path = new_path

    def delete(self) -> None:
        if self.path:
            try:
                Path(self.path).unlink(missing_ok=True)
            except OSError:
                pass
            self.path = None

    def download(
        self,
        session: Any,
        prepare_drm: Optional[Callable[..., Any]] = None,  # noqa: F821
        progress: Optional[Callable[[Dict[str, Any]], Any]] = None,
        cancelled: Optional[Callable[[], bool]] = None,
        **kwargs: Any,
    ) -> Any:
        """Run the DRM licensing + download flow for this track.

        `prepare_drm(drm, track)` callback must license the `drm` object
        (fill its `.keys`) — usually supplied by the dl command, hiding CDM details.

        Subclasses handle the rest (see manifests/*.download_track).
        """
        from ulm.core.manifests import get_manifest_class

        manifest_class = get_manifest_class(self.descriptor)
        if manifest_class is not None:
            return manifest_class.download_track(track=self, session=session, prepare_drm=prepare_drm, progress=progress, cancelled=cancelled, **kwargs)
        raise NotImplementedError(f"No downloader for descriptor {self.descriptor!r}")

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}<[{self.language}] {self.descriptor.name}>"  # noqa

    def __str__(self) -> str:
        base = f"{self.language or '?'} {self.descriptor.name}"  # noqa
        return base + f" ({self.kid})" if self.kid else base


def find_kid_in_init(init: bytes) -> Optional[str]:
    """Extract a CENC Key ID from an mp4 'moov' box ('tenc' boxes hold it).
    Returns a 32-char lowercase hex string, or None."""
    moov = box_find(init, _MOOV)
    if not moov:
        return None
    tenc_hex = re.compile(rb"tenc.{12}.{16}", re.DOTALL)  # slim: tenc is 20+ bytes
    for match in tenc_hex.finditer(moov):
        candidate = match.group(0)

        kid = candidate[-16:].hex()
        if kid.strip("0"):  # all-zero kids are junk
            return kid
    return None


class VideoRange:
    SDR = "SDR"
    HDR10 = "HDR10"
    DV = "DOLBY VISION"
    HDR10Plus = "HDR10"


class Video(Track):
    def __init__(
        self,
        height: Optional[int] = None,
        width: Optional[int] = None,
        range_: str = "SDR",
        fps: Optional[float] = None,
        codec: Optional[str] = None,
        bitrate: Optional[int] = None,
        **kwargs: Any,
    ):
        super().__init__(**kwargs)
        self.height = height
        self.width = width
        self.range = range_
        self.fps = fps
        self.codec = codec
        self.bitrate = bitrate

    def vui_bsf(self) -> List[str]:
        """ffmpeg bsf args that force this track's colour range tags."""
        mapping = {"SDR": "1", "HDR10": "9", "DOLBY VISION": "9"}
        value = mapping.get(self.range)
        if not value:
            return []
        return ["-bsf:v", f"h265_metadata={value}" if "265" in (self.codec or "") else f"h264_metadata={value}"]

    def __str__(self) -> str:
        fps = f"{self.fps:g}" if isinstance(self.fps, int) else (f"{self.fps:.3f}" if self.fps else "?")
        return f"{str(self.language or '?'):<15} | H{self.height or '?':<4} {self.width or '?'}x{self.height or '?'} {self.codec or '?':<6}{self.bitrate or 0:>7} | fps={fps:<6} | {self.range}"


class Audio(Track):
    def __init__(
        self,
        channels: Optional[int] = None,
        codec: Optional[str] = None,
        bitrate: Optional[int] = None,
        atmos: bool = False,
        joc: bool = False,
        **kwargs: Any,
    ):
        super().__init__(**kwargs)
        self.channels = channels
        self.codec = codec
        self.bitrate = bitrate
        self.atmos = atmos
        self.joc = joc

    @property
    def complexity(self) -> str:
        if self.atmos:
            return "Atmos"
        if self.joc:
            return "JOC"
        return ""

    @property
    def force_channels_str(self) -> str:
        return f"{self.channels}ch" if self.channels else "?"

    def __str__(self) -> str:
        return (
            f"{str(self.language or '?'):<15} | {self.force_channels_str:<5} {self.codec or '?':<6}{self.bitrate or 0:>8} "
            f"{'(Atmos)' if self.atmos else ''}"
        )


class Subtitle(Track):
    def __init__(self, subtitle_codec: str = "SRT", forced: bool = False, sdh: bool = False, **kwargs: Any):
        super().__init__(**kwargs)
        self.subtitle_codec = subtitle_codec
        self.forced = bool(forced)
        self.sdh = bool(sdh)

    @property
    def extension(self) -> str:
        return self.subtitle_codec.split(" ")[0].lower() or "srt"

    def convert(self, target_format: str = "srt", output: Optional[Path] = None) -> Path:
        import pysubs2  # imported lazily so `unshackle --help` never pays for it

        if not self.path:
            raise ValueError("Subtitle has not been downloaded yet")
        output = output or Path(tempfile.mkstemp(prefix="Subtitle_", suffix=f".{target_format}", dir=Path(self.path).parent)[1])
        subs = pysubs2.load(str(self.path))
        subs.save(str(output), format=target_format)
        return output

    def __str__(self) -> str:
        return f"{str(self.language or '?'):<15} | {self.subtitle_codec:<6}{(' (Forced)' if self.forced else '')}{(' (SDH)' if self.sdh else '')}"


class Chapter:
    def __init__(self, name: str, timestamp: float):
        self.name = name
        self.timestamp = timestamp

    def __str__(self) -> str:
        return f"{self.name} @{int(self.timestamp)}"


class Chapters:
    def __init__(self, chapters: Optional[Sequence[Chapter]] = None):
        # sort by timestamp while keeping insertion order for equal stamps
        self.chapters = sorted(chapters or [], key=lambda c: (c.timestamp))
        self.path: Optional[Path] = None

    def __len__(self) -> int:
        return len(self.chapters)

    def __iter__(self) -> Iterator[Chapter]:
        return iter(self.chapters)

    def __getitem__(self, i: int) -> Chapter:
        return self.chapters[i]

    def add(self, chapter: Chapter) -> None:
        self.chapters.append(chapter)
        self.chapters.sort(key=lambda c: c.timestamp)

    def write_ogm(self, output: Path) -> Path:
        """OGM-style plain-text chapter file, accepted by mkvmerge."""
        lines = []
        for i, chapter in enumerate(self.chapters, start=1):
            name = chapter.name or f"Chapter {i:02}"
            minutes, seconds = divmod(int(chapter.timestamp), 60)
            hours, minutes = divmod(minutes, 60)
            lines.append(f"CHAPTER{i:02}={hours:02}:{minutes:02}:{seconds:02}")
            lines.append(f"CHAPTER{i:02}NAME={name}")
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.path = output
        return output


class DRMInfo:
    """Lightweight DRM marker attached to a Track.

    Services may attach one of these to say 'this track is encrypted and
    its keys live behind this pssh/system'. The dl pipeline licenses it
    through the CDM layer and fills `.keys` (kid -> hex key).
    """

    def __init__(self, system: DRMSystem, pssh: Union[str, bytes, None] = None, **extra: Any):
        self.system = system
        self.pssh = pssh
        self.keys: Dict[str, str] = {}
        self.kid: Optional[str] = extra.get("kid")
        for key, value in extra.items():
            setattr(self, key, value)

    @property
    def system_name(self) -> str:
        return self.system.value.lower()  # e.g. 'widevine'

    def __repr__(self) -> str:
        return f"DRMInfo<{self.system_name} kid={self.kid} pssh={'yes' if self.pssh else 'no'}>"


class Tracks:
    """Container with categorized lists + selection helpers."""

    def __init__(self, iterable: Optional[Sequence[Track]] = None):
        self.videos: List[Video] = []
        self.audio: List[Audio] = []
        self.subtitles: List[Subtitle] = []
        self.chapters: Chapters = Chapters()
        if iterable:
            for track in iterable:
                self.add(track)

    def add(self, track: Track, warn_only: bool = True) -> None:
        if isinstance(track, Chapters):
            for chapter in track:
                self.chapters.add(chapter)
            return
        if isinstance(track, Video):
            bucket = self.videos
        elif isinstance(track, Audio):
            bucket = self.audio
        elif isinstance(track, Subtitle):
            bucket = self.subtitles
        else:
            log = logging.getLogger(__name__)
            log.warning(f"Unknown track type {type(track).__name__}; storing under data")
            return
        if track in bucket:
            if not warn_only:
                raise ValueError(f"Duplicate track added: {track!r}")
            return
        bucket.append(track)

    def add_tracks(self, tracks: Iterable[Track]) -> None:
        for track in tracks:
            self.add(track)

    def __len__(self) -> int:
        return len(self.videos) + len(self.audio) + len(self.subtitles) + len(self.chapters)

    def __iter__(self) -> Iterator[Track]:
        yield from self.videos
        yield from self.audio
        yield from self.subtitles

    def by_language(self, language: Any) -> List[Track]:
        return [t for t in self if (str(t.language).lower() == str(language).lower() if t.language else False)]

    def sorted_by(self, attr: str, reverse: bool = True) -> List[Track]:
        return sorted([t for t in self if getattr(t, attr, None) is not None], key=lambda t: getattr(t, attr), reverse=reverse)

    def select_video(self, best: bool = True, codec: Optional[str] = None, range_: Optional[str] = None) -> Optional[Video]:
        pool = self.videos
        if codec:
            pool = [v for v in pool if v.codec and codec.lower() in str(v.codec).lower()]
        if range_:
            pool = [v for v in pool if v.range.lower() == range_.lower()]
        pool = sorted(pool, key=lambda v: (v.height or 0, v.bitrate or 0), reverse=best)
        return pool[0] if pool else None

    def select_audio(self, language: Any = None, best: bool = True) -> Optional[Audio]:
        pool = self.by_language(language) if language else self.audio
        pool = sorted(pool, key=lambda a: (a.channels or 0, a.bitrate or 0), reverse=best)
        return pool[0] if pool else None

    def select_subtitles(self, language: Any = None, forced: Optional[bool] = None) -> List[Subtitle]:
        pool = [s for s in self.subtitles]
        if language:
            pool = [s for s in pool if s.language and str(s.language).lower() == str(language).lower()]
        if forced is not None:
            pool = [s for s in pool if s.forced == forced]
        return pool

    def to_dict(self) -> Dict[str, Any]:
        from ulm.core.utils.serialization import track_to_dict

        return {
            "videos": [track_to_dict(v) for v in self.videos],
            "audio": [track_to_dict(a) for a in self.audio],
            "subtitles": [track_to_dict(s) for s in self.subtitles],
            "chapters": [{"name": c.name, "timestamp": c.timestamp} for c in self.chapters],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Tracks":
        tracks = cls()
        for kind in ("videos", "audio", "subtitles"):
            for item in data.get(kind, []):
                tracks.add(_track_from_dict(kind, item))
        tracks.chapters = Chapters([Chapter(c["name"], float(c["timestamp"])) for c in data.get("chapters", [])])
        return tracks


def _track_from_dict(kind: str, item: Dict[str, Any]) -> Track:
    common = dict(
        id_=item.get("id") or None,
        url=item.get("url"),
        descriptor=Descriptor(item.get("descriptor", "SIMPLE")),
        language=item.get("language"),
        is_original_lang=item.get("is_original_lang", False),
        data=item.get("data") or {},
    )
    drm_infos = [DRMInfo(DRMSystem(d["system"]), d.get("pssh")) for d in item.get("drm", [])]
    common["drm"] = drm_infos
    if kind == "videos":
        return Video(
            height=item.get("height"), width=item.get("width"), range_=item.get("range", "SDR"),
            fps=item.get("fps"), codec=item.get("codec"), bitrate=item.get("bitrate"), **common,
        )
    if kind == "audio":
        return Audio(channels=item.get("channels"), codec=item.get("codec"), bitrate=item.get("bitrate"), **common)
    return Subtitle(subtitle_codec=item.get("subtitle_codec", "SRT"), forced=item.get("forced", False), **common)
