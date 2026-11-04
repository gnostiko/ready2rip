# SPDX-License-Identifier: GPL-3.0-or-later
"""CD-TEXT pack decoder (READ TOC / CD-TEXT response body).

Live MMC I/O for CD-TEXT / MCN / ISRC is **not implemented**. Call the stub
readers below if you need a stable API — they always return empty / None and
log once at DEBUG. Album metadata still comes from MusicBrainz / gnudb.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ready2rip.metadata.providers import AlbumMetadata

log = logging.getLogger(__name__)
_STUB_LOGGED = False


@dataclass
class CdTextInfo:
    """Decoded CD-TEXT (block 0, single-byte packs)."""

    album_title: str = ''
    album_artist: str = ''
    genre: str = ''
    track_titles: dict[int, str] = field(default_factory=dict)
    track_artists: dict[int, str] = field(default_factory=dict)


@dataclass
class DiscCodes:
    """Placeholder for MCN / ISRC (MMC I/O not wired)."""

    mcn: str = ''
    isrcs: dict[int, str] = field(default_factory=dict)


def _log_stub_once() -> None:
    global _STUB_LOGGED
    if not _STUB_LOGGED:
        log.debug(
            'CD-TEXT / MCN / ISRC disc reads are not implemented; '
            'use MusicBrainz or gnudb for metadata'
        )
        _STUB_LOGGED = True


def read_cd_text(device: str) -> CdTextInfo | None:
    """Stub: MMC CD-TEXT I/O is not available yet."""
    del device
    _log_stub_once()
    return None


def read_mcn(device: str) -> str:
    """Stub: MMC MCN I/O is not available yet."""
    del device
    _log_stub_once()
    return ''


def read_isrc(device: str, track: int) -> str:
    """Stub: MMC ISRC I/O is not available yet."""
    del device, track
    _log_stub_once()
    return ''


def read_disc_codes(device: str, track_numbers: list[int]) -> DiscCodes:
    """Stub: returns empty MCN/ISRC."""
    del device, track_numbers
    _log_stub_once()
    return DiscCodes()


def enrich_album_from_disc(
    album: AlbumMetadata | None,
    *,
    device: str,
    track_numbers: list[int],
) -> AlbumMetadata | None:
    """Stub: does not enrich from the disc (no MMC CD-TEXT/MCN/ISRC yet)."""
    del device, track_numbers
    _log_stub_once()
    return album


def parse_cd_text(raw: bytes) -> CdTextInfo:
    """Decode a READ TOC CD-TEXT response (header + 18-byte packs)."""
    info = CdTextInfo()
    if len(raw) < 4:
        return info
    # Header: big-endian data length including the next two reserved bytes.
    data_len = (raw[0] << 8) | raw[1]
    packs = raw[4 : 4 + max(0, data_len - 2)]
    buckets: dict[tuple[int, int], bytearray] = {}
    order: list[tuple[int, int]] = []
    for i in range(0, len(packs) - (len(packs) % 18), 18):
        pack = packs[i : i + 18]
        ptype = pack[0]
        track = pack[1]
        block = pack[3] >> 4
        dbcc = bool(pack[3] & 0x80)
        if block != 0 or dbcc:
            continue
        if ptype not in (0x80, 0x81, 0x87):
            continue
        if not _cdtext_crc_ok(pack):
            continue
        key = (ptype, track)
        if key not in buckets:
            buckets[key] = bytearray()
            order.append(key)
        buckets[key].extend(pack[4:16])

    by_type: dict[int, list[tuple[int, bytearray]]] = {}
    for ptype, track in order:
        by_type.setdefault(ptype, []).append((track, buckets[(ptype, track)]))

    for ptype, parts in by_type.items():
        start_track = parts[0][0]
        blob = bytearray()
        for _t, data in parts:
            blob.extend(data)
        strings = _split_nul_strings(bytes(blob))
        for offset, text in enumerate(strings):
            if not text:
                continue
            track_no = start_track + offset
            if ptype == 0x80:  # Title
                if track_no == 0:
                    info.album_title = text
                else:
                    info.track_titles[track_no] = text
            elif ptype == 0x81:  # Performer
                if track_no == 0:
                    info.album_artist = text
                else:
                    info.track_artists[track_no] = text
            elif ptype == 0x87 and track_no == 0:  # Genre (album)
                cleaned = text.lstrip('\x00')
                if len(cleaned) >= 2 and cleaned[0] < ' ' and cleaned[1] < ' ':
                    cleaned = cleaned[2:]
                info.genre = cleaned.strip()
    return info


def _split_nul_strings(data: bytes) -> list[str]:
    out: list[str] = []
    for part in data.split(b'\x00'):
        if not part:
            if out and out[-1] == '':
                continue
            out.append('')
            continue
        try:
            text = part.decode('latin-1').strip()
        except Exception:
            continue
        out.append(text)
    while out and out[-1] == '':
        out.pop()
    return out


def _cdtext_crc_ok(pack: bytes) -> bool:
    if len(pack) < 18:
        return False
    expected = (pack[16] << 8) | pack[17]
    return (_crc16_ccitt(pack[:16]) ^ 0xFFFF) == expected


def _crc16_ccitt(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc
