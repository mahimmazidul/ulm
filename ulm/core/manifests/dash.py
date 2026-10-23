"""DASH (MPD) manifest parser — stdlib `xml.etree`, no lxml.

Supports:
- one-period and multi-period MPDs, BaseURL chains
- SegmentTemplate with SegmentTimeline ($Number$/$Time$ tokens, %0Nd padding)
- SegmentBase / direct BaseURL representations (single .mp4 with byte ranges)
- ContentProtection -> Widevine/PlayReady DRMInfo attachments

Produces Descriptor.DASH tracks whose `data` carries absolute init and
media URLs resolved at parse time, so `download_track` never re-reads XML.
"""
from __future__ import annotations

import logging
import re
import shutil
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urljoin

from ulm.core.constants import Descriptor, DRMSystem
from ulm.core.downloader import download_url
from ulm.core.manifests import Manifest
from ulm.core.tracks import Audio, Subtitle, Track, Video, DRMInfo
from ulm.core.utils.language import make_language

_CODEC_PREFIX = {
    "avc1": "H264", "h264": "H264", "avc3": "H264",
    "hvc1": "H265", "hev1": "H265", "h265": "H265",
    "mp4a": "AAC", "ac-3": "AC3", "ac3": "AC3", "ec-3": "EAC3", "ec3": "EAC3",
    "opus": "Opus", "flac": "FLAC", "srt": "SRT", "stpp": "TTML", "vtt": "WebVTT",
}


def _strip_namespaces(text: str) -> str:
    """Remove xmlns attributes so ElementTree matches nodes by plain names."""
    return re.sub(r'xmlns(:\w+)?="[^"]*"', "", text)


def _num(value: Any) -> Optional[int]:
    try:
        if value in (None, ""):
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _fps(value: Any) -> Optional[float]:
    if not value:
        return None
    text = str(value)
    if "/" in text:
        numerator, _, denominator = text.partition("/")
        try:
            denominator_value = float(denominator)
            return float(numerator) / denominator_value if denominator_value else None
        except (TypeError, ValueError):
            return None
    try:
        return float(text)
    except ValueError:
        return None


def _codec_from(token: str) -> Optional[str]:
    token = (token or "").strip().lower()
    for prefix, name in _CODEC_PREFIX.items():
        if token.startswith(prefix):
            return name
    return None


class DASH(Manifest):
    descriptor: Descriptor = Descriptor.DASH

    def __init__(self, url: str, session: Any, text: Optional[str] = None):
        self.url = url
        self.session = session
        raw = text if text is not None else session.get(url, max_retries=1).text
        self._parse(raw)

    @classmethod
    def matches(cls, url: str) -> bool:
        return url.split("?")[0].lower().endswith(".mpd")

    @classmethod
    def from_url(cls, url: str, session: Any) -> "DASH":
        return cls(url, session)

    # --- parsing -------------------------------------------------------

    def _parse(self, text: str) -> None:
        root = ET.fromstring(_strip_namespaces(text))
        self.attributes = dict(root.attrib)
        root_base_node = root.find("BaseURL")
        base = urljoin(self.url, root_base_node.text.strip()) if root_base_node is not None and root_base_node.text else self.url

        self.periods: List[Dict[str, Any]] = []
        for period_index, period in enumerate(root.findall("Period")):
            period_base_node = period.find("BaseURL")
            period_base = (
                urljoin(base, period_base_node.text.strip())
                if period_base_node is not None and period_base_node.text
                else base
            )
            adaptations: List[Dict[str, Any]] = []
            for adaptation in period.findall("AdaptationSet"):
                adaptation_base_node = adaptation.find("BaseURL")
                adaptation_base = (
                    urljoin(period_base, adaptation_base_node.text.strip())
                    if adaptation_base_node is not None and adaptation_base_node.text
                    else period_base
                )
                role_node = adaptation.find("Role")
                role = (role_node.get("value") if role_node is not None else "") or ""
                protections: List[Dict[str, Any]] = []
                for protection in adaptation.findall("ContentProtection"):
                    scheme = (protection.get("schemeIdUri") or "").lower()
                    pssh_node = protection.find("pssh")
                    pssh = (pssh_node.text or "").strip() if pssh_node is not None else None
                    if "widevine" in scheme:
                        protections.append({"system": DRMSystem.WIDEVINE, "pssh": pssh})
                    elif "playready" in scheme:
                        protections.append({"system": DRMSystem.PLAYREADY, "pssh": pssh})
                adaptations.append(
                    {
                        "role": role,
                        "type": (adaptation.get("contentType") or "").lower(),
                        "mimeType": (adaptation.get("mimeType") or "").lower(),
                        "language": adaptation.get("lang") or self.attributes.get("lang") or "en",
                        "template": _extract_template(adaptation.find("SegmentTemplate")),
                        "base": adaptation_base,
                        "protections": protections,
                        "representations": [
                            dict(representation.attrib)
                            for representation in adaptation.findall("Representation")
                        ],
                    }
                )
            self.periods.append({"base": period_base, "adaptations": adaptations, "index": period_index})

    # --- tracks ---------------------------------------------------------

    def create_tracks(self) -> Any:
        from ulm.core.tracks import Tracks

        tracks = Tracks()
        for period in self.periods:
            for adaptation in period["adaptations"]:
                mime = adaptation["mimeType"]
                for representation in adaptation["representations"]:
                    rep_id = representation.get("id", "")
                    codecs = (representation.get("codecs") or "").split(",")[0]
                    codec_name = _codec_from(codecs)
                    init_url, media_urls = self._expand_segment(adaptation, representation, period["base"])
                    drm: List[DRMInfo] = [DRMInfo(p["system"], p.get("pssh")) for p in adaptation["protections"]]
                    import os as _os

                    common = dict(
                        id_=_os.urandom(4).hex(),
                        url=media_urls[0] if media_urls else "",
                        urls=media_urls,
                        descriptor=Descriptor.DASH,
                        language=make_language(adaptation["language"]) if adaptation["language"] else None,
                        data={
                            "init_segment_url": init_url or "",
                            "period": period["index"],
                            "role": adaptation["role"],
                        },
                        drm=drm,
                    )
                    is_video = "video" in mime or codecs.startswith(("av", "hvc", "hev"))
                    is_audio = "audio" in mime or codecs.startswith(("mp4a", "ac-3", "ac3", "ec-3", "opus", "flac"))
                    if is_video:
                        tracks.add(
                            Video(
                                height=_num(representation.get("height")),
                                width=_num(representation.get("width")),
                                fps=_fps(representation.get("frameRate")),
                                codec=codec_name,
                                bitrate=_num(representation.get("bandwidth")),
                                **common,
                            )
                        )
                    elif is_audio:
                        tracks.add(
                            Audio(
                                codec=codec_name,
                                bitrate=_num(representation.get("bandwidth")),
                                **common,
                            )
                        )
                    elif "subtitle" in mime or codecs.startswith(("stpp", "srt", "vtt")):
                        subtitle_codec = "TTML" if codecs.startswith(("stpp", "ttml")) else ("WebVTT" if codecs.startswith("vtt") else "SRT")
                        tracks.add(Subtitle(subtitle_codec=subtitle_codec, **common))
        return tracks

    @staticmethod
    def _expand_segment(
        adaptation: Dict[str, Any], representation: Dict[str, Any], base: str
    ) -> Tuple[Optional[str], List[str]]:
        """Resolve init + media segment URLs from a template, timeline, or direct BaseURL file."""
        template = adaptation.get("template") or {}
        rep_id = representation.get("id", "")
        rep_base_url = representation.get("BaseURL", "")
        bandwidth = str(representation.get("bandwidth") or "")
        init_url: Optional[str] = None
        media_urls: List[str] = []

        if template:
            init_template = template.get("initialization") or ""
            init_url = (
                urljoin(base, init_template.replace("$RepresentationID$", rep_id).replace("$Bandwidth$", bandwidth))
                if init_template
                else None
            )
            media_template = template.get("media") or ""
            if not media_template:
                return init_url, []
            if "$Number" in media_template:
                times = template.get("times") or []
                count = max(1, len(times))
                pad_match = re.search(r"Number%0(\d+)d\$", media_template)
                pad = int(pad_match.group(1)) if pad_match else 6
                for i in range(count):
                    expanded = re.sub(r"Number%0\d+d\$", str(i + 1).zfill(pad), media_template)
                    expanded = re.sub(r"Number\$", str(i + 1).zfill(pad), expanded)
                    expanded = expanded.replace("$RepresentationID$", rep_id).replace("$Bandwidth$", bandwidth)
                    media_urls.append(urljoin(base, expanded))
            elif "$Time" in media_template:
                times = template.get("times") or []
                for timestamp in times:
                    expanded = re.sub(r"Time(%.*)?\$", str(timestamp), media_template)
                    expanded = re.sub(r"Time\$", str(timestamp), expanded)
                    expanded = expanded.replace("$RepresentationID$", rep_id).replace("$Bandwidth$", bandwidth)
                    media_urls.append(urljoin(base, expanded))
            else:
                expanded = media_template.replace("$RepresentationID$", rep_id).replace("$Bandwidth$", bandwidth)
                media_urls.append(urljoin(base, expanded))
            return init_url, media_urls

        if rep_base_url:
            media_urls.append(urljoin(base, str(rep_base_url)))
        else:
            media_urls.append(urljoin(base, rep_id))
        return init_url, media_urls

    # --- download --------------------------------------------------------

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
        """Download init + segment files into a temp dir, concatenate."""
        log = logging.getLogger(cls.__name__)
        tmp_dir = Path(tempfile.mkdtemp(prefix="ush-dash-"))
        files: List[Path] = []
        init_url = (track.data or {}).get("init_segment_url")
        if init_url:
            init_path = tmp_dir / "000000-init.mp4"
            init_path.write_bytes(session.get(init_url, max_retries=1).content)
            files.append(init_path)
        total_urls = len([u for u in track.urls if u])
        for index, media_url in enumerate(track.urls):
            if not media_url:
                continue
            path = tmp_dir / f"{index + 1:08d}.mp4"
            download_url(
                session,
                media_url,
                path,
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


def _extract_template(node: Optional[ET.Element]) -> Optional[Dict[str, Any]]:
    if node is None:
        return None
    timeline = node.find("SegmentTimeline")
    times: List[int] = []
    if timeline is not None:
        running: Optional[int] = None
        for element in timeline.findall("S"):
            start = element.get("t")
            duration_value = int(element.get("d", "0") or 0)
            repeat = int(element.get("r", "0") or 0)
            if start is not None:
                running = int(start)
            if running is None:
                continue
            for _ in range(repeat + 1):
                times.append(running)
                running += duration_value
    return {
        "initialization": node.get("initialization"),
        "media": node.get("media"),
        "times": times,
        "startNumber": int(node.get("startNumber", "1") or 1),
        "presentationTimeOffset": int(node.get("presentationTimeOffset", "0") or 0),
    }
