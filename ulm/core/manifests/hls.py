"""HLS (m3u8) manifest parser — pure Python, no external `m3u8` library.

Parses master playlists (#EXT-X-STREAM-INF variants, #EXT-X-MEDIA audio
and subtitle renditions) and media playlists (#EXT-X-MAP init sections,
#EXT-X-KEY tags, EXTINF segments).

The downloader streams segments through `core.downloader.download_segments`,
applies AES-128 decryption when the playlist carries an identity key,
and marks Widevine/PlayReady DRM for the dl pipeline to license.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urljoin

import requests

from ulm.core.constants import Descriptor, DownloadCancelled, DRMSystem
from ulm.core.downloader import download_segments
from ulm.core.manifests import Manifest
from ulm.core.tracks import Audio, Subtitle, Track, Video
from ulm.core.utils.language import make_language

CODEC_MAP = {
    "avc1": "H264", "h264": "H264", "avc3": "H264",
    "hvc1": "H265", "hev1": "H265", "h265": "H265",
    "mp4a": "AAC", "ac-3": "AC3", "ac3": "AC3",
    "ec-3": "EAC3", "ec3": "EAC3",
    "stpp": "TTML", "wvtt": "WebVTT", "subt": "SRT",
}

_ATTR_PAIR = re.compile(r'([A-Z0-9-]+)=("([^"]*)"|[^,]*)')


@dataclass
class SegmentInfo:
    """One EXTINF entry of a media playlist."""

    url: str
    init_url: str = ""
    key_method: Optional[str] = None
    key_format: Optional[str] = None
    key_uri: Optional[str] = None
    key_iv: Optional[str] = None


@dataclass
class PlaylistInfo:
    """A parsed media playlist."""
    url: str
    init_section_url: Optional[str] = None
    segments: List[SegmentInfo] = field(default_factory=list)
    key_method: Optional[str] = None
    key_format: Optional[str] = None
    key_uri: Optional[str] = None
    key_iv: Optional[str] = None
    codec: Optional[str] = None
    resolution: Tuple[Optional[int], Optional[int]] = (None, None)
    fps_hidden: Optional[float] = None
    bandwidth: Optional[int] = None


def _parse_attrs(raw: str) -> Dict[str, str]:
    attrs: Dict[str, str] = {}
    for match in _ATTR_PAIR.finditer(raw):
        value = match.group(3) if match.group(3) is not None else match.group(2)
        attrs[match.group(1)] = value
    return attrs


def parse_media_playlist(url: str, text: str) -> PlaylistInfo:
    """Parse one media playlist fully into absolute segment URLs."""
    info = PlaylistInfo(url=url)
    current_method: Optional[str] = None
    current_format: Optional[str] = None
    current_uri: Optional[str] = None
    current_iv: Optional[str] = None
    staged = False
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#EXT-X-MAP:"):
            attrs = _parse_attrs(line[len("#EXT-X-MAP:") :])
            if attrs.get("URI"):
                info.init_section_url = urljoin(url, attrs["URI"])
        elif line.startswith("#EXT-X-KEY:"):
            attrs = _parse_attrs(line[len("#EXT-X-KEY:") :])
            if attrs.get("METHOD") == "NONE":
                current_method, current_format, current_uri, current_iv = None, None, None, None
            else:
                current_method = attrs.get("METHOD")
                current_format = attrs.get("KEYFORMAT")
                current_uri = urljoin(url, attrs["URI"]) if attrs.get("URI") else None
                current_iv = attrs.get("IV")
        elif line.startswith("#EXTINF"):
            staged = True
        elif staged and not line.startswith("#"):
            staged = False
            info.segments.append(
                SegmentInfo(
                    url=urljoin(url, line),
                    init_url=info.init_section_url or "",
                    key_method=current_method,
                    key_format=current_format,
                    key_uri=current_uri,
                    key_iv=current_iv,
                )
            )
        if info.key_method is None and current_method:
            info.key_method = current_method
            info.key_format = current_format
            info.key_uri = current_uri
            info.key_iv = current_iv
    return info


class HLS(Manifest):
    descriptor: Descriptor = Descriptor.HLS

    def __init__(self, url: str, session: Any, text: Optional[str] = None):
        self.url = url
        self.session = session
        self.text = text if text is not None else session.get(url, max_retries=1).text
        self.variant_tracks: List[Dict[str, Any]] = []  # raw EXT-X-STREAM-INF entries
        self.audio_renditions: List[Dict[str, Any]] = []
        self.subtitle_renditions: List[Dict[str, Any]] = []
        self._parse_master(url, self.text)

    @classmethod
    def matches(cls, url: str) -> bool:
        path = url.split("?")[0].lower()
        return path.endswith((".m3u8", ".m3u")) or "format=m3u8" in url.lower()

    @classmethod
    def from_url(cls, url: str, session: Any) -> "HLS":
        return cls(url, session)

    @classmethod
    def from_text(cls, url: str, text: str, session: Any) -> "HLS":
        return cls(url, session, text=text)

    # --- parsing ---------------------------------------------------------

    @staticmethod
    def _absolute(base: str, relative: str) -> str:
        return urljoin(base, relative)

    def _parse_master(self, base: str, text: str) -> None:
        current: Optional[Dict[str, Any]] = None
        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                continue
            if line.startswith("#EXT-X-STREAM-INF:"):
                attrs = _parse_attrs(line[len("#EXT-X-STREAM-INF:") :])
                current = {"attrs": attrs, "uri": ""}
                self.variant_tracks.append(current)
            elif line.startswith("#EXT-X-MEDIA:"):
                pending = _parse_attrs(line[len("#EXT-X-MEDIA:") :])
                if pending.get("TYPE") == "AUDIO":
                    self.audio_renditions.append(pending)
                elif pending.get("TYPE") == "SUBTITLES":
                    self.subtitle_renditions.append(pending)
            elif not line.startswith("#") and current is not None and not current.get("uri"):
                current["uri"] = self._absolute(base, line)
        if not self.variant_tracks:
            # Media playlist passed directly (no EXT-X-STREAM-INF): treat it
            # as a single pseudo-variant download_track can handle.
            self.variant_tracks.append({"attrs": {}, "uri": base, "direct": True})

    # --- track creation ------------------------------------------------------

    def create_tracks(self) -> Any:
        from ulm.core.tracks import Tracks

        tracks = Tracks()

        for position, variant in enumerate(self.variant_tracks):
            uri = variant.get("uri")
            attrs = variant.get("attrs", {})
            resolution = str(attrs.get("RESOLUTION", "") or "")
            width, _, height = resolution.partition("x")
            codecs = attrs.get("CODECS", "")
            video_codec = self._video_from_codecs(codecs)
            bandwidth = int(attrs["BANDWIDTH"]) if attrs.get("BANDWIDTH", "").isdigit() else None
            if uri and (width or height or bandwidth):
                tracks.add(
                    Video(
                        id_=f"video-{position}",
                        url=uri,
                        descriptor=Descriptor.HLS,
                        width=int(width) if width.isdigit() else None,
                        height=int(height) if height.isdigit() else None,
                        codec=video_codec,
                        bitrate=bandwidth,
                        data={"uri": uri, "codecs": codecs},
                    )
                )
            elif uri and variant.get("direct"):
                tracks.add(
                    Video(
                        id_=f"video-{position}",
                        url=uri,
                        descriptor=Descriptor.HLS,
                        data={"uri": uri, "codecs": codecs},
                    )
                )

        for position, rendition in enumerate(self.audio_renditions):
            uri = rendition.get("URI")
            if not uri:
                continue
            tracks.add(
                Audio(
                    id_=f"audio-{position}",
                    url=self._absolute(self.url, uri),
                    descriptor=Descriptor.HLS,
                    language=make_language(rendition.get("LANGUAGE") or rendition.get("NAME")),
                    is_original_lang=(rendition.get("DEFAULT") or "").upper() == "YES",
                    data={"uri": self._absolute(self.url, uri), "channels": self._audio_channels(rendition.get("CHANNELS", ""))},
                )
            )

        for position, rendition in enumerate(self.subtitle_renditions):
            uri = rendition.get("URI")
            if not uri:
                continue
            codecs = rendition.get("CODECS", "")
            if "ttml" in codecs.lower():
                sub_codec = "TTML"
            elif "srt" in codecs.lower():
                sub_codec = "SRT"
            else:
                sub_codec = "WebVTT"
            forced = (rendition.get("FORCED") or "").upper() == "YES"
            tracks.add(
                Subtitle(
                    id_=f"subtitle-{position}",
                    url=self._absolute(self.url, uri),
                    descriptor=Descriptor.HLS,
                    language=make_language(rendition.get("LANGUAGE") or rendition.get("NAME")),
                    subtitle_codec=sub_codec,
                    forced=forced,
                    data={"uri": self._absolute(self.url, uri), "format": sub_codec},
                )
            )
        return tracks

    @staticmethod
    def _video_from_codecs(codecs: str) -> Optional[str]:
        for token in (codecs or "").split(","):
            token = token.strip().lower()
            for prefix, name in CODEC_MAP.items():
                if token.startswith(prefix):
                    return name
        return None

    @staticmethod
    def _audio_channels(raw: str) -> Optional[int]:
        raw = (raw or "").strip().rstrip('"')
        try:
            return int(raw)
        except ValueError:
            return None

    # --- downloading ---------------------------------------------------------

    @classmethod
    def download_track(
        cls,
        track: Track,
        session: Any,
        prepare_drm: Optional[Callable[..., Any]] = None,
        progress: Optional[Callable[[Dict[str, Any]], Any]] = None,
        cancelled: Optional[Callable[[], bool]] = None,
        **kwargs: Any,
    ) -> Optional[Path]:
        """Stream segments of this track's media playlist, concat into one file,
        decrypt per-segment when the playlist carries AES-128 identity keys."""
        log = logging.getLogger(cls.__name__)
        media_uri = track.data.get("uri") or track.url
        if not media_uri:
            raise ValueError(f"{track} has no media playlist URI")

        response = session.get(media_uri, max_retries=1)
        response.raise_for_status()
        playlist = parse_media_playlist(media_uri, response.text)
        if not playlist.segments and not playlist.init_section_url:
            raise ValueError(f"{media_uri} parsed to 0 segments")

        init_path: Optional[Path] = None
        tmp_dir = Path(tempfile.mkdtemp(prefix="ush-hls-"))
        if playlist.init_section_url:
            init_path = tmp_dir / "00-init.mp4"
            init_path.write_bytes(session.get(playlist.init_section_url, max_retries=1).content)

        segment_paths = download_segments(
            session,
            [s.url for s in playlist.segments],
            tmp_dir,
            filename_template="{index:08d}",
            headers=dict(track.data.get("headers", {})),
            cookies=getattr(session, "cookies", None),
            max_workers=int(track.data.get("max_workers", 4)) or 4,
            on_progress=progress,
            cancelled=cancelled,
            extension=".mp4",
        )
        if cancelled and cancelled():
            raise DownloadCancelled()

        if prepare_drm:
            for drm_info in track.drm:
                if not drm_info.keys:
                    prepare_drm(drm_info, track)

        # AES-128 identity keys: fetch the key, then decrypt every segment in place.
        key_method = playlist.key_method or (
            playlist.segments[0].key_method if playlist.segments else None
        )
        key_uri = playlist.key_uri or next((s.key_uri for s in playlist.segments if s.key_uri), None)
        if key_method == "AES-128" and key_uri:
            cipher_key = _fetch_identity_key(session, key_uri)
            for seg_path in segment_paths:
                _decrypt_aes128(seg_path, cipher_key)

        if init_path is None and playlist.init_section_url:
            init_path = tmp_dir / "00-init.mp4"
            init_path.write_bytes(session.get(playlist.init_section_url, max_retries=1).content)

        out_tmp = Path(tempfile.mkstemp(prefix="ush-track-", suffix=".mp4")[1])
        files: List[Path] = []
        if init_path:
            files.append(init_path)
        files.extend(segment_paths)
        _concat_files(files, out_tmp)

        shutil.rmtree(tmp_dir, ignore_errors=True)  # segments are scratch
        log.debug("HLS download_track done: %s", out_tmp)
        return out_tmp


def _fetch_identity_key(session: Any, key_uri: str) -> Optional[bytes]:
    """Fetch an AES-128 identity key (hex or raw 16-byte)."""
    try:
        if key_uri.startswith("data:"):
            import base64

            payload = key_uri.split(",", 1)[1]
            data = base64.b64decode(payload) if ";base64" in key_uri else payload.encode("utf-8")
        else:
            data = session.get(key_uri, max_retries=1).content
    except Exception:
        return None
    try:
        return bytes.fromhex(data.decode("ascii").strip())
    except (ValueError, UnicodeDecodeError):
        return data[:16] if isinstance(data, bytes) and len(data) >= 16 else None


def _decrypt_aes128(path: Path, key: Optional[bytes]) -> None:
    if not key:
        return
    from Crypto.Cipher import AES  # pycryptodomex (pywidevine dependency)

    data = path.read_bytes()
    cipher = AES.new(key, AES.MODE_CBC, iv=b"\x00" * 16)
    path.write_bytes(cipher.decrypt(data))


def _concat_files(files: List[Path], output: Path) -> None:
    """Append every file into `output` with a bounded 4 MiB buffer."""
    with output.open("wb") as out:
        for path in files:
            with path.open("rb") as src:
                shutil.copyfileobj(src, out, length=1 << 22)
