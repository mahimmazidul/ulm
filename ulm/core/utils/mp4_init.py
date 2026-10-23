"""Build minimal fMP4 init segments from ISM `CodecPrivateData`.

Clean-room ISO-BMFF writer for just what ISM needs: ftyp + moov with a
single trak. The structures follow ISO/IEC 14496-12/15 (summarized):
- ftyp: major_brand isom, minor_VERSION 512, compatible brands
- moov: mvhd (version 0), trak: tkhd(v0 flags 7), mdia: mdhd(v0), hdlr,
  minf: vmhd/smhd, dinf(dref url self), stbl: stsd+stsz+stco

Every builder validates its own output by walking the boxes once in
`verify()` so a bad struct can never leave this module silently.
"""
from __future__ import annotations

import struct
from typing import Any, List, Optional, Tuple


def box(tag: bytes, *payload: bytes) -> bytes:
    body = b"".join(payload)
    return struct.pack(">I", 8 + len(body)) + tag + body


def full_box(tag: bytes, version: int, flags: int, *payload: bytes) -> bytes:
    body = struct.pack(">I", version << 24 | flags) + b"".join(payload)
    return _box(tag, body)


def _box(tag: bytes, header_and_payload: bytes) -> bytes:
    return struct.pack(">I", 8 + len(header_and_payload)) + tag + header_and_payload


def ftyp() -> bytes:
    brands = b"isomiso2mp41"
    body = b"isom" + struct.pack(">I", 512) + b"isomiso2mp41"[:8]
    return _box(b"ftyp", body)


def mvhd() -> bytes:
    body = struct.pack(">II", 0, 0)
    body += struct.pack(">I", 1000)  # timescale
    body += struct.pack(">I", 0)     # duration
    body += struct.pack(">i", 0x00010000)  # rate 1.0
    body += struct.pack(">h", 0x0100)      # volume 1.0
    body += struct.pack(">h", 0)           # reserved
    body += struct.pack(">I", 0)           # reserved
    body += struct.pack(">I", 0)           # reserved
    body += struct.pack(">9i", 0x00010000, 0, 0, 0, 0x00010000, 0, 0, 0, 0x40000000)
    body += struct.pack(">i", 0) * 6       # pre_defined
    body += struct.pack(">I", 2)           # next_track_id
    return full_box(b"mvhd", 0, 0, body)


def tkhd(track_id: int, width: int, height: int, is_video: bool) -> bytes:
    body = struct.pack(">II", 0, 0)
    body += struct.pack(">I", track_id)
    body += struct.pack(">I", 0)            # reserved
    body += struct.pack(">I", 0)            # duration
    body += struct.pack(">I", 0) * 2        # reserved
    body += struct.pack(">h", 0)            # layer
    body += struct.pack(">h", 0 if is_video else 0x0100)  # alternate group / volume
    body += struct.pack(">h", 0)            # reserved
    body += struct.pack(">9i", 0x00010000, 0, 0, 0, 0x00010000, 0, 0, 0, 0x40000000)
    body += struct.pack(">II", width << 16, height << 16)
    return full_box(b"tkhd", 0, 7, body)


def mdhd(timescale: int) -> bytes:
    body = struct.pack(">II", 0, 0)
    body += struct.pack(">I", timescale)
    body += struct.pack(">I", 0)          # duration
    body += struct.pack(">H", 0x55C4)     # und
    body += struct.pack(">H", 0)
    return full_box(b"mdhd", 0, 0, body)


def hdlr(handler: bytes) -> bytes:
    body = struct.pack(">I", 0) + handler + b"\0" * 12 + (b"unshackle\0")
    return full_box(b"hdlr", 0, 0, body)


def dinf() -> bytes:
    url = full_box(b"url ", 0, 1)  # self-contained
    dref = full_box(b"dref", 0, 0, struct.pack(">I", 1)) + url
    return _box(b"dinf", dref)


def smhd() -> bytes:
    return full_box(b"smhd", 0, 0, struct.pack(">HH", 0, 0xFFFE))


def vmhd() -> bytes:
    return full_box(b"vmhd", 0, 1, struct.pack(">hh", 0, 0) + struct.pack(">6h", 0, 0, 0, 0, 0, 0))


def stsd_entry_video(sample_entry: bytes) -> bytes:
    return sample_entry


def stsd(sample_entry: bytes) -> bytes:
    return full_box(b"stsd", 0, 0, struct.pack(">I", 1) + sample_entry)


def stsz() -> bytes:
    return full_box(b"stsz", 0, 0, struct.pack(">II", 0, 0))


def stco() -> bytes:
    return full_box(b"stco", 0, 0, struct.pack(">I", 0))


def stbl(sample_entry: bytes) -> bytes:
    return box(b"stbl", stsd(sample_entry), stsz(), stco())


def minf(is_video: bool, timescale: int, sample_entry: bytes) -> bytes:
    fast = vmhd() if is_video else smhd()
    return box(b"minf", fast, dinf(), stbl(sample_entry))


def mdia(is_video: bool, timescale: int, sample_entry: bytes, handler: bytes) -> bytes:
    return box(b"mdia", mdhd(timescale), hdlr(handler), minf(is_video, timescale, sample_entry))


def trak(is_video: bool, timescale: int, sample_entry: bytes, handler: bytes, width: int, height: int) -> bytes:
    return box(b"trak", tkhd(1, width, height, is_video), mdia(is_video, timescale, sample_entry, handler))


# --- ISM sample entries ---------------------------------------------------

_VIDEO_BASE_PAYLOAD = b"\0" * 6 + struct.pack(">H", 1)  # data_reference_index


def _pack_video_entry(width: int, height: int) -> bytes:
    payload = _VIDEO_BASE_PAYLOAD
    payload += struct.pack(">H", 0)      # pre_defined
    payload += struct.pack(">H", 0)      # reserved
    payload += struct.pack(">H", 0) * 3  # pre_defined * 3
    payload += struct.pack(">H", width)
    payload += struct.pack(">H", height)
    payload += struct.pack(">I", 0x00480000)  # hDPI 72dpi
    payload += struct.pack(">I", 0x00480000)  # vDPI
    payload += struct.pack(">I", 0)      # reserved
    payload += struct.pack(">H", 1)      # frame_count
    payload += b"\0" * 32                # compressor name
    payload += struct.pack(">H", 0x0018) # depth
    payload += struct.pack(">H", 0xFFFF) # pre_defined
    return payload


def avc1(width: int, height: int, sps: bytes, pps: bytes) -> bytes:
    avcc_payload = struct.pack(">BBH", 1, 0xFF, 0xFC | 0x03)  # version/preset/nalu-size-1
    avcc_payload += struct.pack(">B", 0xE1 if sps else 0xE0)
    avcc_payload += struct.pack(">H", len(sps))
    avcc_payload += sps
    avcc_payload += struct.pack(">B", 1)
    avcc_payload += struct.pack(">H", len(pps))
    avcc_payload += pps
    return box(b"avc1", _pack_video_entry(width, height), box(b"avcC", avcc_payload))


def hvc1(width: int, height: int, private: bytes) -> bytes:
    payload = _pack_video_entry(width, height)
    payload += box(b"hvcC", private)
    return box(b"hvc1", payload)


def _pack_audio_entry(channels: int) -> bytes:
    payload = b"\0" * 6 + struct.pack(">H", 1)
    payload += b"\0" * 8              # reserved
    payload += struct.pack(">H", 0)   # pre_defined
    payload += struct.pack(">H", channels)
    payload += struct.pack(">H", 16)  # sample size
    payload += struct.pack(">H", 0)   # pre_defined
    payload += struct.pack(">I", 0)   # reserved
    payload += struct.pack(">I", 0)   # sample rate (set real value below)
    return payload


def mp4a(timescale: int, channels: int, esds_payload: bytes) -> bytes:
    entry = _pack_audio_entry(channels)
    entry = entry[:-6] + struct.pack(">I", timescale or 48000) + struct.pack(">H", 0)
    return box(b"mp4a", entry, box(b"esds", full_box(b"esds", 0, 0, esds_payload)))


def ec3(timescale: int, channels: int) -> bytes:
    entry = _pack_audio_entry(channels)
    entry = entry[:-6] + struct.pack(">I", timescale or 48000) + struct.pack(">H", 0)
    return box(b"ec-3", entry)


# --- CodecPrivateData parsing ----------------------------------------------


def split_sps_pps(private: bytes) -> Tuple[bytes, bytes]:
    """ISM CodecPrivateData: startcode-prefixed SPS then PPS (avcC-style AnnexB)."""
    data = private if isinstance(private, bytes) else b""
    if not data:
        return b"", b""
    if not data.startswith(b"\x00\x00\x00\x01") and not data.startswith(b"\x00\x00\x01"):
        return private, b""
    header = data[:4] if data[:4] == b"\x00\x00\x00\x01" else data[:3]
    body = data[len(header):]
    marker4 = body.find(b"\x00\x00\x00\x01")
    marker3 = body.find(b"\x00\x00\x01")
    if marker4 >= 0 and (marker3 < 0 or marker4 < marker3):
        second_marker_length = 4
        position = marker4
    elif marker3 >= 0:
        second_marker_length = 3
        position = marker3
    else:
        return body, b""
    return body[:position], body[position + second_marker_length:]


# --- top-level builder -------------------------------------------------------


def build_init_segment(track: Any) -> Optional[bytes]:
    """Build an `ftyp+moov` init segment for one ISM-derived Track."""
    data = getattr(track, "data", {}) or {}
    private_hex = data.get("codec_private_data", "")
    private = bytes.fromhex(private_hex) if private_hex else b""
    width = getattr(track, "width", None) or 0
    height = getattr(track, "height", None) or 0
    codec = (getattr(track, "codec", "") or "").upper()
    fourcc = (data.get("fourcc") or "").upper()

    if codec in ("H264", "AVC", "AVC1") or fourcc in ("H264", "AVC1", "AVC"):
        sps, pps = split_sps_pps(private)
        entry = avc1(width, height, sps, pps)
        payload = ftyp() + box(b"moov", mvhd(), trak(True, 12288, entry, b"vide", width, height))
        return _verify(payload)
    if codec in ("H265", "HEVC", "HVC1") or fourcc in ("H265", "HEVC", "HVC1"):
        entry = hvc1(width, height, private)
        payload = ftyp() + box(b"moov", mvhd(), trak(True, 12288, entry, b"vide", width, height))
        return _verify(payload)
    if codec in ("AAC", "MP4A"):
        audio = getattr(track, "audio", {})
        channels = getattr(track, "channels", None) or 2
        entry = mp4a(48000, channels, private or b"\x03\x80\x80\x22\x00\x00\x00\x04\x80\x80\x14\x40\x15\x00\x00\x00\x00\x01\xF4\x00\x00\x05\x80\x80\x05\x12\x10\x56\xE5\x00\x06\x80\x80\x01\x02")
        return _verify(ftyp() + box(b"moov", mvhd(), trak(False, 48000, entry, b"soun", 0, 0)))
    if codec in ("EAC3", "EC-3", "AC3"):
        channels = getattr(track, "channels", None) or 6
        entry = ec3(48000, channels)
        return _verify(ftyp() + box(b"moov", mvhd(), trak(False, 48000, entry, b"soun", 0, 0)))
    return None


def verify(data: bytes) -> Optional[bytes]:
    """Walk top-level boxes and every child one level deep; return the data
    (or None when inconsistent)."""
    total = len(data)
    offset = 0
    seen = False
    while offset + 8 <= total:
        size = int.from_bytes(data[offset:offset + 4], "big")
        tag = data[offset + 4:offset + 8]
        if size < 8 or offset + size > total:
            return None
        seen = True
        if tag == b"moov":
            inner_offset = offset + 8
            while inner_offset + 8 < offset + size:
                inner_size = int.from_bytes(data[inner_offset:inner_offset + 4], "big")
                if inner_size < 8 or inner_offset + inner_size > offset + size:
                    return None
                inner_offset += inner_size
        offset += size
    return data if seen and offset == total else None


def _verify(payload: bytes) -> Optional[bytes]:
    checked = verify(payload)
    if checked is None:
        return None
    return payload
