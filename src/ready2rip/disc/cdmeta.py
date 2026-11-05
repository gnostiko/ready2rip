# SPDX-License-Identifier: GPL-3.0-or-later
"""CD-TEXT / MCN / ISRC from the disc (MMC) plus CD-TEXT pack decoding.

Live reads use READ TOC format 5 (CD-TEXT) and READ SUB-CHANNEL formats
02h/03h (MCN / ISRC). Online MusicBrainz / gnudb metadata still wins when
present; disc fields only fill empty gaps.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field, replace

from ready2rip.metadata.providers import AlbumMetadata, TrackMetadata

log = logging.getLogger(__name__)

_ISRC_RE = re.compile(r'^[A-Z]{2}[A-Z0-9]{3}\d{7}$')
_MCN_RE = re.compile(r'^\d{12,13}$')


@dataclass
class CdTextInfo:
    """Decoded CD-TEXT (block 0, single-byte packs)."""

    album_title: str = ''
    album_artist: str = ''
    genre: str = ''
    track_titles: dict[int, str] = field(default_factory=dict)
    track_artists: dict[int, str] = field(default_factory=dict)

    @property
    def has_useful(self) -> bool:
        return bool(
            self.album_title
            or self.album_artist
            or self.genre
            or self.track_titles
            or self.track_artists
        )


@dataclass
class DiscCodes:
    """MCN (barcode) and per-track ISRC from subchannel Q."""

    mcn: str = ''
    isrcs: dict[int, str] = field(default_factory=dict)


def read_cd_text(device: str) -> CdTextInfo | None:
    """Read and decode CD-TEXT from *device*, or None when absent/unavailable."""
    from ready2rip.disc.mmc import MmcDevice

    try:
        with MmcDevice(device, nonblock=False) as mmc:
            raw = mmc.read_cd_text_raw()
    except OSError as exc:
        log.debug('CD-TEXT open failed on %s: %s', device, exc)
        return None
    if not raw:
        return None
    info = parse_cd_text(raw)
    if not info.has_useful:
        return None
    return info


def read_mcn(device: str) -> str:
    """Return the disc media catalog number (UPC/EAN), or ``''``."""
    from ready2rip.disc.mmc import MmcDevice

    try:
        with MmcDevice(device, nonblock=False) as mmc:
            raw = mmc.read_mcn_raw()
    except OSError as exc:
        log.debug('MCN open failed on %s: %s', device, exc)
        return ''
    return parse_mcn_response(raw) if raw else ''


def read_isrc(device: str, track: int) -> str:
    """Return the ISRC for *track*, or ``''``."""
    from ready2rip.disc.mmc import MmcDevice

    try:
        with MmcDevice(device, nonblock=False) as mmc:
            raw = mmc.read_isrc_raw(track)
    except OSError as exc:
        log.debug('ISRC open failed on %s track %s: %s', device, track, exc)
        return ''
    return parse_isrc_response(raw) if raw else ''


def read_disc_codes(device: str, track_numbers: list[int]) -> DiscCodes:
    """Read MCN once and ISRCs for each track in *track_numbers*."""
    from ready2rip.disc.mmc import MmcDevice

    codes = DiscCodes()
    try:
        with MmcDevice(device, nonblock=False) as mmc:
            mcn_raw = mmc.read_mcn_raw()
            if mcn_raw:
                codes.mcn = parse_mcn_response(mcn_raw)
            for num in track_numbers:
                raw = mmc.read_isrc_raw(int(num))
                if not raw:
                    continue
                isrc = parse_isrc_response(raw)
                if isrc:
                    codes.isrcs[int(num)] = isrc
    except OSError as exc:
        log.debug('Disc codes open failed on %s: %s', device, exc)
    return codes


def enrich_album_from_disc(
    album: AlbumMetadata | None,
    *,
    device: str,
    track_numbers: list[int],
) -> AlbumMetadata | None:
    """Fill empty album/track fields from CD-TEXT / MCN / ISRC.

    Never overwrites non-empty online or user-edited values. Returns a new
    album when CD-TEXT alone can seed metadata, otherwise *album* (possibly
    replaced with an updated copy) or ``None``.
    """
    cdtext = read_cd_text(device)
    codes = read_disc_codes(device, track_numbers)
    has_disc_meta = bool(
        (cdtext is not None and cdtext.has_useful) or codes.mcn or codes.isrcs
    )
    if not has_disc_meta:
        return album

    if album is None:
        album = AlbumMetadata(source='cd-text' if cdtext else 'disc')
        if track_numbers:
            album.tracks = [
                TrackMetadata(number=n) for n in sorted(set(int(t) for t in track_numbers))
            ]

    changed = False
    title = album.title
    artist = album.artist
    genre = album.genre
    barcode = album.barcode
    source = album.source or ''

    if cdtext is not None:
        if not title and cdtext.album_title:
            title = cdtext.album_title
            changed = True
        if not artist and cdtext.album_artist:
            artist = cdtext.album_artist
            changed = True
        if not genre and cdtext.genre:
            genre = cdtext.genre
            changed = True

    if not barcode and codes.mcn:
        barcode = codes.mcn
        changed = True

    tracks = list(album.tracks)
    track_by_num = {t.number: t for t in tracks}
    # Ensure track slots exist only when disc meta can fill them.
    for num in sorted(set(int(t) for t in track_numbers) | set(codes.isrcs)):
        if num in track_by_num:
            continue
        has_track_meta = num in codes.isrcs or (
            cdtext is not None
            and (num in cdtext.track_titles or num in cdtext.track_artists)
        )
        if has_track_meta:
            track_by_num[num] = TrackMetadata(number=num)
            changed = True

    for num, track in list(track_by_num.items()):
        new_title = track.title
        new_artist = track.artist
        new_isrc = track.isrc
        if cdtext is not None:
            if not new_title and num in cdtext.track_titles:
                new_title = cdtext.track_titles[num]
            if not new_artist and num in cdtext.track_artists:
                new_artist = cdtext.track_artists[num]
        if not new_isrc and num in codes.isrcs:
            new_isrc = codes.isrcs[num]
        if (
            new_title != track.title
            or new_artist != track.artist
            or new_isrc != track.isrc
        ):
            track_by_num[num] = replace(
                track, title=new_title, artist=new_artist, isrc=new_isrc
            )
            changed = True

    if not changed:
        return album

    new_tracks = [track_by_num[n] for n in sorted(track_by_num)]
    new_source = source
    if cdtext is not None and (not source or source == 'manual'):
        new_source = 'cd-text'
    elif codes.mcn or codes.isrcs:
        if not source or source == 'manual':
            new_source = 'disc'

    return replace(
        album,
        title=title,
        artist=artist,
        genre=genre,
        barcode=barcode,
        tracks=new_tracks,
        source=new_source,
    )


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


def parse_mcn_response(raw: bytes) -> str:
    """Extract ASCII MCN/UPC from a READ SUB-CHANNEL format 02h buffer."""
    if len(raw) < 16:
        return ''
    # Byte 4 = format (02h); byte 5 bit7 = MCVal when valid.
    if raw[4] != 0x02:
        return ''
    if not (raw[5] & 0x80):
        return ''
    # Catalog is 13 ASCII digits starting at offset 8 (MMC / libcdio).
    catalog = raw[8:21]
    text = _clean_code(catalog)
    if _MCN_RE.match(text):
        return text
    # Some drives pad differently — take digit run.
    digits = ''.join(c for c in text if c.isdigit())
    return digits if _MCN_RE.match(digits) else ''


def parse_isrc_response(raw: bytes) -> str:
    """Extract ISRC from a READ SUB-CHANNEL format 03h buffer."""
    if len(raw) < 16:
        return ''
    if raw[4] != 0x03:
        return ''
    if not (raw[5] & 0x80):  # TCVal
        return ''
    # ISRC is 12 ASCII chars starting at offset 8.
    text = _clean_code(raw[8:20]).upper()
    if _ISRC_RE.match(text):
        return text
    # Tolerate missing country separators by stripping spaces.
    compact = re.sub(r'[^A-Z0-9]', '', text)
    return compact if _ISRC_RE.match(compact) else ''


def _clean_code(data: bytes) -> str:
    try:
        text = data.decode('ascii', errors='ignore')
    except Exception:
        return ''
    return ''.join(ch for ch in text if ch.isalnum()).strip()


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
