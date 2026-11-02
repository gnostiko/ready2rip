# SPDX-License-Identifier: GPL-3.0-or-later
"""Disc subchannel codes (MCN/ISRC) and CD-TEXT via MMC."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from ready2rip.disc.mmc import MmcDevice
from ready2rip.metadata.providers import AlbumMetadata, TrackMetadata

log = logging.getLogger(__name__)

_ISRC_RE = re.compile(r'^[A-Z0-9]{12}$')
_MCN_RE = re.compile(r'^[0-9]{12,13}$')


@dataclass
class CdTextInfo:
    """Decoded CD-TEXT (block 0, single-byte packs)."""

    album_title: str = ''
    album_artist: str = ''
    genre: str = ''
    track_titles: dict[int, str] = field(default_factory=dict)
    track_artists: dict[int, str] = field(default_factory=dict)

    def to_album(self, *, track_count: int = 0) -> AlbumMetadata | None:
        if not self.album_title and not self.track_titles:
            return None
        n = track_count or max(self.track_titles.keys(), default=0)
        tracks: list[TrackMetadata] = []
        for num in range(1, n + 1):
            tracks.append(
                TrackMetadata(
                    number=num,
                    title=self.track_titles.get(num, ''),
                    artist=self.track_artists.get(num, '') or self.album_artist,
                )
            )
        return AlbumMetadata(
            title=self.album_title,
            artist=self.album_artist,
            genre=self.genre,
            tracks=tracks,
            source='cd-text',
        )


@dataclass
class DiscCodes:
    mcn: str = ''
    isrcs: dict[int, str] = field(default_factory=dict)


def read_mcn(device: str) -> str:
    """Media Catalog Number (UPC/EAN) from Q-subchannel, or empty."""
    try:
        with MmcDevice(device, nonblock=False) as mmc:
            raw = mmc.read_subchannel(param=0x02)
    except OSError as exc:
        log.debug('MCN open %s failed: %s', device, exc)
        return ''
    return _parse_mcn(raw)


def read_isrc(device: str, track: int) -> str:
    """ISRC for *track* (1–99), or empty."""
    if track < 1 or track > 99:
        return ''
    try:
        with MmcDevice(device, nonblock=False) as mmc:
            raw = mmc.read_subchannel(param=0x03, track=track)
    except OSError as exc:
        log.debug('ISRC open %s track %s failed: %s', device, track, exc)
        return ''
    return _parse_isrc(raw)


def read_disc_codes(device: str, track_numbers: list[int]) -> DiscCodes:
    codes = DiscCodes(mcn=read_mcn(device))
    for num in track_numbers:
        isrc = read_isrc(device, num)
        if isrc:
            codes.isrcs[num] = isrc
    return codes


def read_cd_text(device: str) -> CdTextInfo | None:
    """Read and decode CD-TEXT, or None when absent / unsupported."""
    try:
        with MmcDevice(device, nonblock=False) as mmc:
            raw = mmc.read_cd_text()
    except OSError as exc:
        log.debug('CD-TEXT open %s failed: %s', device, exc)
        return None
    if not raw:
        return None
    info = parse_cd_text(raw)
    if not info.album_title and not info.track_titles and not info.album_artist:
        return None
    return info


def enrich_album_from_disc(
    album: AlbumMetadata | None,
    *,
    device: str,
    track_numbers: list[int],
) -> AlbumMetadata | None:
    """Fill missing MCN/ISRC (and empty titles from CD-TEXT) from the disc."""
    codes = read_disc_codes(device, track_numbers)
    cdtext = None
    need_text = album is None or (
        not (album.title or '').strip()
        and not any((t.title or '').strip() for t in album.tracks)
    )
    if need_text:
        cdtext = read_cd_text(device)

    if album is None:
        if cdtext is None and not codes.mcn and not codes.isrcs:
            return None
        album = (
            cdtext.to_album(track_count=max(track_numbers) if track_numbers else 0)
            if cdtext is not None
            else AlbumMetadata(source='cd-text')
        )
        if album is None:
            album = AlbumMetadata(source='disc')

    if codes.mcn and not (album.mcn or album.barcode):
        album.mcn = codes.mcn
        if not album.barcode:
            album.barcode = codes.mcn

    by_num = {t.number: t for t in album.tracks}
    for num, isrc in codes.isrcs.items():
        track = by_num.get(num)
        if track is None:
            track = TrackMetadata(number=num)
            album.tracks.append(track)
            by_num[num] = track
        if isrc and not track.isrc:
            track.isrc = isrc

    if cdtext is not None:
        if not (album.title or '').strip() and cdtext.album_title:
            album.title = cdtext.album_title
        if not (album.artist or '').strip() and cdtext.album_artist:
            album.artist = cdtext.album_artist
        if not (album.genre or '').strip() and cdtext.genre:
            album.genre = cdtext.genre
        if not album.source:
            album.source = 'cd-text'
        for num, title in cdtext.track_titles.items():
            track = by_num.get(num)
            if track is None:
                track = TrackMetadata(number=num)
                album.tracks.append(track)
                by_num[num] = track
            if title and not (track.title or '').strip():
                track.title = title
            artist = cdtext.track_artists.get(num) or cdtext.album_artist
            if artist and not (track.artist or '').strip():
                track.artist = artist

    album.tracks.sort(key=lambda t: t.number)
    return album


def parse_cd_text(raw: bytes) -> CdTextInfo:
    """Decode a READ TOC CD-TEXT response (header + 18-byte packs)."""
    info = CdTextInfo()
    if len(raw) < 4:
        return info
    # Header: big-endian data length including the next two reserved bytes.
    data_len = (raw[0] << 8) | raw[1]
    packs = raw[4 : 4 + max(0, data_len - 2)]
    # Accumulate text bytes per (type, first track of sequence).
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

    # Merge continuation packs that share type with increasing track seq —
    # libcdio-style: NUL-separated strings in the concatenated stream, starting
    # at the track number of the first pack for that type.
    by_type: dict[int, list[tuple[int, bytearray]]] = {}
    for ptype, track in order:
        by_type.setdefault(ptype, []).append((track, buckets[(ptype, track)]))

    for ptype, parts in by_type.items():
        # Concatenate in pack order; first track is the album (0) or track N.
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
                # Genre pack often starts with two genre-code bytes.
                cleaned = text.lstrip('\x00')
                if len(cleaned) >= 2 and cleaned[0] < ' ' and cleaned[1] < ' ':
                    cleaned = cleaned[2:]
                info.genre = cleaned.strip()
    return info


def _split_nul_strings(data: bytes) -> list[str]:
    out: list[str] = []
    for part in data.split(b'\x00'):
        if not part:
            # Preserve empty slots from consecutive NULs (pack padding ends).
            if out and out[-1] == '':
                continue
            out.append('')
            continue
        try:
            text = part.decode('latin-1').strip()
        except Exception:
            continue
        # Stop at trailing padding runs of empty.
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


def _parse_mcn(raw: bytes | None) -> str:
    if not raw or len(raw) < 24:
        return ''
    # Subchannel data header (4) + MCN descriptor: data len, audio status,
    # ADR/control, then 13 ASCII digits + zero + AFRAME…
    # Absolute layout after SG payload: bytes[4:] is sub-Q data list.
    data = raw[4:] if len(raw) >= 4 else raw
    if len(data) < 16:
        return ''
    # Media catalog: byte 0 = data length, 1 = audio status, 2 = ADR/control,
    # 3..15 = 13 digit chars (ASCII).
    digits = bytes(b for b in data[3:16] if 48 <= b <= 57)
    text = digits.decode('ascii', errors='ignore')
    if _MCN_RE.match(text):
        return text
    return ''


def _parse_isrc(raw: bytes | None) -> str:
    if not raw or len(raw) < 24:
        return ''
    data = raw[4:] if len(raw) >= 4 else raw
    if len(data) < 16:
        return ''
    # Track ISRC descriptor: similar header, then 12 ASCII chars.
    chars = bytes(b for b in data[3:15] if 48 <= b <= 57 or 65 <= b <= 90)
    text = chars.decode('ascii', errors='ignore')
    if _ISRC_RE.match(text):
        return text
    return ''
