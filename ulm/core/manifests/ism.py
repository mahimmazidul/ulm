"""ISM (Smooth Streaming) manifest parser — stdlib xml.etree.

Smooth Streaming manifests reference an ISM Manifest XML with StreamIndex
entries of QualityLevel fragments. We build absolute segment URLs from the
base URL + QualityLevel Bitrate + fragment Time values, attach DRM info
fromProtection, and produce a synthesized init segment when the server
doesn't provide one (from `CodecPrivateData`).
"""
from __future__ import annotations

import base64
import logging
import re
import shutil
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse, urljoin

from ulm.core.constants import Descriptor, DRMSystem
from ulm.core.downloader import download_url
from ulm.core.manifests import Manifest
from ulm.core.tracks import Audio, Subtitle, Track, Video, DRMInfo
from ulm.core.utils.language import make_language

_CODEC = {
    "H264": "H264", "WVC1": "VC1", "VC1": "VC1",
    "AACL": "AAC", "AAC": "AAC", "AC3": "AC3", "EC3": "EAC3", "EAC3": "EAC3",
    "TTML": "TTML", "DFXP": "TTML", "WVTT": "WebVTT",
}


def _codec_from(fourcc: str) -> Optional[str]:
    fourcc = (fourcc or "").upper().strip()
    return _CODEC.get(fourcc)


def _channel_count(raw: Any) -> Optional[int]:
    try:
        channels = int(str(raw))
    except (TypeError, ValueError):
        return None
    return {1: 1, 2: 2, 3: 3, 5: 5, 6: 6, 8: 8}.get(channels)


def _codec_private_data_to_bytes(private: str) -> bytes:
    return bytes.fromhex(private)


class ISM(Manifest):
    descriptor: Descriptor = Descriptor.ISM

    def __init__(self, url: str, session: Any, text: Optional[str] = None):
        self.url = url
        self.session = session
        raw = text if text is not None else session.get(url, max_retries=1).text
        self._parse(raw)

    @classmethod
    def matches(cls, url: str) -> bool:
        path = url.split("?")[0].lower()
        return path.endswith((".ism", "/manifest", "/manifest()"))

    @classmethod
    def from_url(cls, url: str, session: Any) -> "ISM":
        return cls(url, session)

    def _parse(self, text: str) -> None:
        root = ET.fromstring(_strip_namespaces(text))
        base_url = self.url.rsplit("?", 1)[0].rsplit("/", 1)[0] + "/"
        ism_base_url_nodes = root.findall("BaseURL")
        if ism_base_url_nodes:
            base_url = urljoin(base_url, (ism_base_url_nodes[0].text or "").strip())
        self.base_url = base_url

        drm: List[Dict[str, Any]] = []
        protection_node = root.find("Protection")
        if protection_node is not None:
            for header in protection_node.findall("ProtectionHeader"):
                system_id = (header.get("SystemID") or "").strip()
                raw_data = (header.text or "").strip()
                try:
                    xml_data = base64.b64decode(raw_data).decode("utf-8", "replace")
                except Exception:
                    xml_data = raw_data
                if "playready" in system_id.lower():
                    drm.append({"system": DRMSystem.PLAYREADY, "protection": xml_data})
        self.protections = drm

        self.streams: List[Dict[str, Any]] = []
        for stream in root.findall("StreamIndex"):
            stream_type = stream.get("Type", "").lower()
            fourcc = stream.get("FourCC", "") or ""
            language = make_language(stream.get("Language") or "en")
            name = stream.get("Name") or ""

            stream_url = stream.get("Url", "")
            stream_base_nodes = stream.findall("BaseURL")
            if stream_base_nodes:
                stream_url = urljoin(base_url, (stream_base_nodes[0].text or "").strip())
            else:
                stream_url = urljoin(base_url, stream_url.replace("{bitrate}", "") if "{bitrate}" in stream_url else stream_url)

            qualities: List[Dict[str, Any]] = []
            for quality in stream.findall("QualityLevel"):
                index = quality.get("Index")
                qualities.append(
                    {
                        "index": index,
                        "bitrate": int(quality.get("Bitrate", "0") or 0),
                        "width": int(quality.get("MaxWidth") or quality.get("Width") or 0) or None,
                        "height": int(quality.get("MaxHeight") or quality.get("Height") or 0) or None,
                        "codec": _codec_from(quality.get("FourCC", fourcc)),
                        "codec_private_data": quality.get("CodecPrivateData", "") or "",
                        "fourcc": (quality.get("FourCC", fourcc) or "").upper(),
                        "channels": _channel_count(quality.get("Channels")) if stream_type == "audio" else None,
                        "sampling_rate": int(quality.get("SamplingRate", "0") or 0),
                        "sub_type": quality.get("Subtype"),
                        "language": language,
                    }
                )
            fragments: List[str] = []
            for fragment in stream.findall("c"):
                fragment_time = fragment.get("t")
                fragment_duration = fragment.get("d")
                fragment_repeat = int(fragment.get("r", "0") or 0)
                if fragment_time is None:
                    continue
                time_value = int(fragment_time)
                for _ in range(fragment_repeat + 1):
                    fragments.append(str(time_value))
                    time_value += int(fragment_duration or 0)

            self.streams.append(
                {
                    "type": stream_type,
                    "qualities": qualities,
                    "fragments": fragments,
                    "url": stream_url,
                    "name": name,
                    "language": language,
                    "drm": drm,
                    "max_height": max((q["height"] or 0) for q in qualities) or None,
                }
            )

    def create_tracks(self) -> Any:
        from ulm.core.tracks import Tracks

        tracks = Tracks()
        import os as _os

        for stream in self.streams:
            for quality in stream["qualities"]:
                urls: List[str] = []
                for fragment_time in stream["fragments"]:
                    base = stream["url"].replace("{bitrate}", str(quality["bitrate"]))
                    urls.append(f"{base}({fragment_time})" if "(" not in base else base)
                if not urls:
                    base = stream["url"].replace("{bitrate}", str(quality["bitrate"]))
                    urls.append(base)
                drm_infos = [DRMInfo(d["system"], d.get("pssh")) for d in stream["drm"]]
                common: Dict[str, Any] = dict(
                    id_=_os.urandom(4).hex(),
                    url=urls[0],
                    urls=urls,
                    descriptor=Descriptor.ISM,
                    language=quality["language"],
                    data={
                        "bitrate": quality["bitrate"],
                        "codec_private_data": quality["codec_private_data"],
                        "fourcc": quality["fourcc"],
                        "period": 0,
                        "needs_init": True,   # synth init from CodecPrivateData on download
                    },
                    drm=drm_infos,
                )
                if stream["type"] == "video":
                    tracks.add(Video(
                        height=quality["height"],
                        width=quality["width"],
                        codec=quality["codec"],
                        bitrate=quality["bitrate"],
                        **common,
                    ))
                elif stream["type"] == "audio":
                    tracks.add(Audio(codec=quality["codec"], bitrate=quality["bitrate"], channels=quality["channels"], **common))
                elif stream["type"] == "text":
                    tracks.add(Subtitle(subtitle_codec=_codec_from(quality["fourcc"]) or "TTML", **common))
        return tracks

    @classmethod
    def download_track(
        cls,
        track: Track,
        session: Any,
        prepare_drm: Optional[Callable[..., Any]] = None,
        progress: Optional[Callable[[Dict[str, Any]], Any]] = None,
        cancelled: Optional[Callable[[], bool]] = None,
        **kwargs: Any,
    ) -> Path:
        """Download the synthesized init + fragment files, then concatenate."""
        tmp_dir = Path(tempfile.mkdtemp(prefix="ush-ism-"))
        files: List[Path] = []
        if (track.data or {}).get("needs_init"):
            init = build_init_segment(track)
            if init:
                init_path = tmp_dir / "000000-init.mp4"
                init_path.write_bytes(init)
                files.append(init_path)

        total_urls = len([u for u in track.urls if u])
        for index, fragment_url in enumerate(track.urls):
            if not fragment_url:
                continue
            path = tmp_dir / f"{index + 1:08d}.mp4"
            download_url(
                session, fragment_url, path,
                cookies=getattr(session, "cookies", None),
                cancelled=cancelled,
                on_progress=progress,
            )
            files.append(path)
            if progress:
                progress({"segments_done": index + 1, "segments_total": total_urls})
            if cancelled and cancelled():
                raise KeyboardInterrupt

        if prepare_drm:
            for drm_info in track.drm:
                if not drm_info.keys:
                    prepare_drm(drm_info, track)

        out_path = Path(tempfile.mkstemp(prefix="ush-track-", suffix=".mp4")[1])
        with out_path.open("wb") as out:
            for path in files:
                with path.open("rb") as src:
                    shutil.copyfileobj(src, out, 1 << 22)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return out_path


def _strip_namespaces(text: str) -> str:
    """Remove xmlns attrs; ISM mixes 2009/2010 namespaces.
    (IISM: namespace prefixes must go too.)"""
    text = re.sub(r'xmlns(:\w+)?="[^"]*"', "", text)
    return text


def build_init_segment(track: Track) -> Optional[bytes]:
    """Synthesize a minimal fMP4 init segment (ftyp+moov) for ISM tracks.

    Handles H264 (avc1 with SPS/PPS from CodecPrivateData), H265 (hvc1),
    AAC (mp4a with esds), EAC3/AC3.
    """
    try:
        from ulm.core.utils.mp4_init import build_init_segment as _build

        return _build(track)
    except ImportError:
        logging.getLogger(__name__).warning("MP4 init synth unavailable; fragmented concat may fail")
        return None


def _codec_child_data(codec_private: str) -> bytes:
    """Decode hex CodecPrivateData into raw bytes (SPS/PPS for H.264)."""
    return bytes.fromhex(codec_private) if codec_private else b""
