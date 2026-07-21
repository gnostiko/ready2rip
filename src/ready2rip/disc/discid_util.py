# SPDX-License-Identifier: GPL-3.0-or-later
"""MusicBrainz DiscID helpers (libdiscid via ctypes, with TOC fallback)."""

from __future__ import annotations

import base64
import ctypes
import ctypes.util
import hashlib
import logging
from dataclasses import dataclass

from ready2rip.disc.probe import DiscInfo

log = logging.getLogger(__name__)

# Red Book: 2 seconds of pregap = 150 frames
PREGAP_FRAMES = 150


@dataclass(frozen=True)
class DiscIdentifiers:
    """IDs derived from the disc table of contents."""

    musicbrainz_discid: str | None = None
    freedb_id: str | None = None
    first_track: int = 1
    last_track: int = 0
    # MB-style offsets: [leadout, track1_start, track2_start, ...] in frames
    offsets: tuple[int, ...] = ()


def identifiers_from_disc(info: DiscInfo) -> DiscIdentifiers:
    """Compute identifiers from an already-probed TOC (no extra device I/O).

    Prefer pure TOC math over ``libdiscid_read``: re-opening the drive can block
    for tens of seconds after cdparanoia, which left the UI stuck on
    \"Looking for an audio CD…\" during deferred startup.
    """
    if not info.tracks:
        return DiscIdentifiers(
            musicbrainz_discid=None,
            freedb_id=info.freedb_id,
            first_track=1,
            last_track=0,
            offsets=(),
        )

    offsets = _toc_to_mb_offsets(info)
    mb_id = _musicbrainz_discid_from_offsets(1, info.track_count, offsets)
    freedb = info.freedb_id or _freedb_id_from_offsets(offsets, info.track_count)
    return DiscIdentifiers(
        musicbrainz_discid=mb_id,
        freedb_id=freedb,
        first_track=1,
        last_track=info.track_count,
        offsets=offsets,
    )


def _toc_to_mb_offsets(info: DiscInfo) -> tuple[int, ...]:
    """Convert cdparanoia LBA starts/lengths to MusicBrainz frame offsets."""
    if not info.tracks:
        return ()
    track_starts = [t.start_sector + PREGAP_FRAMES for t in info.tracks]
    last = info.tracks[-1]
    leadout = last.start_sector + last.length_sectors + PREGAP_FRAMES
    return tuple([leadout, *track_starts])


def _musicbrainz_discid_from_offsets(
    first: int,
    last: int,
    offsets: tuple[int, ...],
) -> str | None:
    """Pure-Python MusicBrainz DiscID (same algorithm as libdiscid)."""
    if last < first or len(offsets) < (last - first + 2):
        return None

    # Try libdiscid put first for exact compatibility.
    put = _libdiscid_put(first, last, offsets)
    if put is not None:
        return put

    parts = [f'{first:02X}{last:02X}']
    # 100 offset slots, zero-padded; offset[0] is lead-out
    for i in range(100):
        if i < len(offsets):
            parts.append(f'{offsets[i]:08X}')
        else:
            parts.append('00000000')
    toc_string = ''.join(parts)
    digest = hashlib.sha1(toc_string.encode('ascii')).digest()
    # MusicBrainz DiscID alphabet: standard base64 with + → .  / → _  = → -
    b64 = base64.b64encode(digest).decode('ascii')
    return b64.replace('+', '.').replace('/', '_').replace('=', '-')


def _freedb_id_from_offsets(offsets: tuple[int, ...], track_count: int) -> str | None:
    """Compute FreeDB/CDDB disc ID from MB-style offsets."""
    if track_count < 1 or len(offsets) < track_count + 1:
        return None

    def _sum_digits(n: int) -> int:
        total = 0
        while n > 0:
            total += n % 10
            n //= 10
        return total

    # Track starts in seconds (MB offsets include 150-frame pregap)
    n = 0
    for i in range(1, track_count + 1):
        n += _sum_digits(offsets[i] // 75)

    leadout = offsets[0]
    first = offsets[1]
    t = (leadout // 75) - (first // 75)
    disc_id = ((n % 0xFF) << 24) | (t << 8) | track_count
    return f'{disc_id:08x}'


def _load_libdiscid() -> ctypes.CDLL | None:
    name = ctypes.util.find_library('discid')
    if not name:
        # Common paths on Linux
        for candidate in ('libdiscid.so.0', 'libdiscid.so'):
            try:
                return ctypes.CDLL(candidate)
            except OSError:
                continue
        return None
    try:
        return ctypes.CDLL(name)
    except OSError:
        return None


def _libdiscid_put(
    first: int,
    last: int,
    offsets: tuple[int, ...],
) -> str | None:
    lib = _load_libdiscid()
    if lib is None:
        return None

    lib.discid_new.restype = ctypes.c_void_p
    lib.discid_free.argtypes = [ctypes.c_void_p]
    lib.discid_put.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.POINTER(ctypes.c_int),
    ]
    lib.discid_put.restype = ctypes.c_int
    lib.discid_get_id.argtypes = [ctypes.c_void_p]
    lib.discid_get_id.restype = ctypes.c_char_p

    # offsets array: [leadout, t1, t2, ...] — libdiscid expects 100 ints max
    arr_type = ctypes.c_int * 100
    arr = arr_type()
    for i, value in enumerate(offsets):
        if i >= 100:
            break
        arr[i] = int(value)

    handle = lib.discid_new()
    if not handle:
        return None
    try:
        if not lib.discid_put(handle, first, last, arr):
            return None
        disc_id = lib.discid_get_id(handle)
        if not disc_id:
            return None
        return disc_id.decode('ascii')
    except Exception:  # noqa: BLE001
        log.exception('libdiscid put failed')
        return None
    finally:
        lib.discid_free(handle)
