"""`ulm dl` — download tracks (manifests, DRM keys, mux).

Stages, each in its own function (read top to bottom = the pipeline):
  1. auth: where cookies are loaded
  2. select: track filters by language/codec/height/range
  3. keys:  DRM licensing + vault lookup/save
  4. download: per-track streaming download via manifests
  5. decrypt: mp4decrypt / shaka
  6. mux + rename: mkvmerge/ffmpeg + output template
"""
from __future__ import annotations

import base64
import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import click

from ulm.core import services as services_mod
from ulm.core.config import config
from ulm.core.console import ProgressLine, log
from ulm.core.downloader import download_segments, download_url
from ulm.core.manifests import detect_descriptor
from ulm.core.session import Session
from ulm.core.tracks import Audio, Subtitle, Track, Tracks, Video
from ulm.core.titles import Episode, Movie
from ulm.core.vaults import load_vaults

log = logging.getLogger("dl")


@click.command("dl", short_help="Download media")
@click.argument("target", nargs=1)
@click.option("--service", "-s", default=None, help="Service tag (e.g. NF). Auto-detected from config otherwise.")
@click.option("-w", "--workers", default=4, show_default=True, help="Segment download workers.")
@click.option("--cookies", "cookie_file", default=None, type=click.Path(exists=True), help="Netscape cookie file path.")
@click.option("--proxy", default=None, help="Proxy URL passed to requests.")
@click.option("--sub-format", default="srt", show_default=True, help="Target subtitle format.")
@click.option("--no-vault", is_flag=True, default=False, help="Skip all vault read/write.")
@click.option("--speed-limit", "speed_limit", default="0", help="Max download speed, e.g. 20MB, 150M, 2G. 0 = off.")
@click.option("--lists", is_flag=True, default=False, help="List tracks then exit.")
def cli(
    target: str,
    service: Optional[str],
    workers: int,
    cookie_file: Optional[str],
    proxy: Optional[str],
    sub_format: str,
    no_vault: bool,
    speed_limit: str,
    lists: bool,
):
    """Download from a manifest URL or service title id."""
    from ulm.core.downloader import set_speed_limit
    from ulm.core.utils.speedparse import parse_speed

    set_speed_limit(parse_speed(speed_limit))
    session = _make_session(proxy, cookie_file)

    vaults = None if no_vault else load_vaults()
    if service:
        service_class = services_mod.load_service(service)
        instance = service_class(ctx=None)
        titles = instance.get_titles()
        if not len(titles):
            log.error("No titles found.")
            return
        for title in titles:
            _run_title(instance, title, session, vaults, workers, sub_format, lists)
        return
    _run_url(target, session, vaults, workers, sub_format, lists)


def _make_session(proxy: Optional[str], cookie_file: Optional[str]) -> Session:
    session = Session()
    headers = config.headers
    if headers:
        session.headers.update(headers)
    if proxy:
        session.proxies.update({"all": proxy})
    if cookie_file:
        from ulm.core.utils.cookies import load_netscape

        for cookie in load_netscape(Path(cookie_file)):
            session.cookies.set(cookie["name"], cookie["value"], domain=cookie.get("domain") or None)
    return session


# --- Stage 0: run a direct URL (manifest) -----------------------------------


def _run_url(url: str, session: Session, vaults: Any, workers: int, sub_format: str, lists: bool) -> None:
    descriptor = detect_descriptor(url)
    manifest_class = None
    if descriptor:
        from ulm.core.manifests import get_manifest_class

        manifest_class = get_manifest_class(descriptor)
    if manifest_class is None:
        log.error("Unknown manifest format for %s", url)
        return
    instance = manifest_class.from_url(url, session)
    tracks = instance.create_tracks()
    _run_tracks(tracks, session, vaults, workers, sub_format, lists, output_suggest=Path("output"))


# --- Stage 0.5: per service title -------------------------------------------


def _run_title(instance: Any, title: Any, session: Session, vaults: Any, workers: int, sub_format: str, lists: bool) -> None:
    tracks = instance.get_tracks(title)
    output = Path(config.directories.downloads) / (getattr(title, "name", "title") or "title")
    _run_tracks(
        tracks,
        session,
        vaults,
        workers,
        sub_format,
        lists,
        output_suggest=output,
        title=title,
        service=instance,
    )


# --- The pipeline --------------------------------------------------------------


def _run_tracks(
    tracks: Tracks,
    session: Session,
    vaults: Any,
    workers: int,
    sub_format: str,
    lists: bool,
    output_suggest: Path,
    title: Any = None,
    service: Any = None,
) -> None:
    if lists:
        from ulm.core.utils.listing import list_tracks

        list_tracks(tracks)
        return

    for track in _select(tracks):
        if isinstance(track, Subtitle):
            _download_subtitle(track, session, output_suggest, sub_format)
            continue
        if not _license_track(track, session, vaults, service):
            log.error(f"Skipping {track}: no keys available")
            continue
        output_dir = Path(config.directories.temp) / "seg"
        manifest_instance = _manifest_instance(track, session)
        if manifest_instance is None:
            path = output_dir / f"{track.id}.mp4"
            download_url(session, track.url, path, cancelled=lambda: False)
        else:
            path = manifest_class_download(track, session, workers)
        _decrypt_and_mux(track, path, output_suggest, title, tracks)


def _select(tracks: Tracks) -> List[Track]:
    """Pick the best video, best audio per language, and all subtitles."""
    picked: List[Track] = []
    best_video = tracks.select_video(best=True)
    if best_video:
        picked.append(best_video)
    languages: List[str] = sorted({str(a.language) for a in tracks.audio if a.language})
    for language in languages:
        best_audio = tracks.select_audio(language=language, best=True)
        if best_audio:
            picked.append(best_audio)
    if not languages:
        best_audio = tracks.select_audio(best=True)
        if best_audio:
            picked.append(best_audio)
    picked.extend(tracks.subtitles)
    return picked


def _manifest_instance(track: Track, session: Session) -> Any:
    descriptor_class_lookup = {
        "HLS": "ulm.core.manifests.hls",
        "DASH": "ulm.core.manifests.dash",
        "ISM": "ulm.core.manifests.ism",
    }
    descriptor_name = track.descriptor.name if hasattr(track.descriptor, "name") else str(track.descriptor)
    if descriptor_name not in descriptor_class_lookup:
        return None
    module = __import__(descriptor_class_lookup[descriptor_name], fromlist=["*"])
    manifest_cls = getattr(module, descriptor_name)
    media_uri = (track.data or {}).get("uri") or track.url
    return manifest_cls(media_uri, session) if media_uri else None


def manifest_class_download(track: Track, session: Session, workers: int) -> Path:
    descriptor_name = track.descriptor.name if hasattr(track.descriptor, "name") else str(track.descriptor)
    from ulm.core.manifests import get_manifest_class

    manifest = get_manifest_class(track.descriptor)
    if manifest is None:
        path = Path(config.directories.temp) / "seg" / f"{track.id}.mp4"
        download_url(session, track.url, path)
        return path
    track.data["max_workers"] = workers
    return manifest.download_track(track=track, session=session, prepare_drm=None, cancelled=lambda: False, progress=None)


def _download_subtitle(track: Subtitle, session: Session, output_dir: Path, sub_format: str) -> None:
    url = (track.data or {}).get("uri") or track.url
    if not url:
        return
    response = session.get(url, max_retries=1)
    temp_path = Path(config.directories.temp) / f"subtitle_{track.id}.{track.extension}"
    temp_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path.write_bytes(response.content)
    track.path = temp_path
    try:
        converted = track.convert(target_format=sub_format)
        text = converted.read_text(encoding="utf-8", errors="replace")
        name = f"{getattr(track.language, 'display_name', '?') or '?'}_{'Forced_' if track.forced else ''}{'SDH_' if track.sdh else ''}subtitle.{sub_format}"
        destination = output_dir / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text, encoding="utf-8")
        temp_path.unlink(missing_ok=True)
        converted.unlink(missing_ok=True)
    except Exception as e:
        log.warning(f"Subtitle convert/format ({sub_format}) failed: {e}")


def _license_track(track: Track, session: Session, vaults: Any, service: Any) -> bool:
    """Pull keys from the vault, or run the licensing flow through the CDM."""
    if not track.drm:
        return True  # unencrypted

    for drm_info in track.drm:
        if drm_info.keys:
            continue
        kid = drm_info.kid or _kid_from_pssh(session, drm_info)
        if kid and vaults:
            found = vaults.find_key(kid, _service_tag(service))
            if found:
                drm_info.keys = {found["kid"]: found["key"]}
        if drm_info.keys:
            continue
        _run_licence(drm_info, track, session, service, vaults)
    return any(drm.keys for drm in track.drm)


def _kid_from_pssh(session: Session, drm_info: Any) -> Optional[str]:
    """Get a KID out of the pssh box header when the service omitted it."""
    import re

    pssh = drm_info.pssh
    if not pssh:
        return None
    if isinstance(pssh, bytes):
        pssh_blob = pssh
    else:
        try:
            pssh_blob = base64.b64decode(pssh + "=" * (-len(pssh) % 4))
        except Exception:
            return None
    return _kid_of(pssh_blob)


def _kid_of(blob: bytes) -> Optional[str]:
    """KID lives at offset 32 for 16 bytes in standard cenc pssh boxes."""
    blob = blob.strip()
    match = blob.find(b"pssh") if b"pssh" in blob else -1
    if match == -1:
        return None
    offset = match + 4 + 4 + 4 + 16  # tag + version/flags + systemid + datasize skipped
    try:
        return blob[offset:offset + 16].hex()
    except Exception:
        return None


def _service_tag(service: Any) -> str:
    if service is None:
        return "unknown"
    tag = getattr(service, "TAG", None) or service.__class__.__name__
    return str(tag).upper()


def _run_licence(drm_info: Any, track: Track, session: Session, service: Any, vaults: Any) -> None:
    """License one widevine DRM through the local / remote CDM."""
    import ulm.core.cdm as cdm_mod

    if drm_info.system.value == "WIDEVINE":
        pssh = drm_info.pssh or ""
        cdm = cdm_mod.load_cdm()
        keys = cdm_mod.get_content_keys(
            cdm,
            pssh,
            licence_fn=lambda challenge: service.get_widevine_license(challenge=challenge, title=None, track=track, **_extra_service_kwargs(track)),
        )
        drm_info.keys.update(keys)
        if vaults and keys:
            for key_kid, key_value in keys.items():
                vaults.add_key(key_kid, key_value, _service_tag(service))
    else:
        log.warning(f"DRM {drm_info.system} licence flow not wired yet; use a pre-filled key vault")


def _extra_service_kwargs(track: Track) -> Dict[str, Any]:
    return {}


# --- Stage: decrypt + mux ------------------------------------------------------------


def _decrypt_and_mux(track: Track, downloaded: Path, output_dir: Path, title: Any, tracks: Tracks) -> None:
    """Decrypt (when the track has keys) then mux and rename."""
    from ulm.core.muxer import decrypt_track_for_mux, mux

    track.path = downloaded
    for drm_info in track.drm:
        if drm_info.keys and downloaded.exists():
            kid, key = next(iter(drm_info.keys.items()))
            try:
                decrypted = decrypt_track_for_mux(downloaded, kid, key)
                os.replace(decrypted, downloaded)
            except Exception as e:
                log.warning(f"decrypt failed: {e}")
                break

    output_dir.mkdir(parents=True, exist_ok=True)
    name = (getattr(title, "name", None) if title else None) or "ulm"
    output = output_dir / f"{name}.mkv"
    try:
        final = mux(tracks, title, output)
        log.log("success", f"Muxed → {final}")
    except Exception as e:
        log.warning(f"Mux failed ({e}); keeping raw tracks in temp dir")


__all__ = ("cli",)
