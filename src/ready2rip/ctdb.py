# SPDX-License-Identifier: GPL-3.0-or-later
"""CUETools Database (CTDB) lookup and track CRC verification.

Protocol follows CUETools / fre:ac notes: colon-separated TOC lookup via
``lookup2.php``, per-track CRC32 over raw CDDA PCM with first/last truncation
matching ``CDRepairEncode.TrackCRC`` (stride 5880).
"""

from __future__ import annotations

import logging
import urllib.error
import urllib.parse
import urllib.request
import wave
import xml.etree.ElementTree as ET
import zlib
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path

from ready2rip.disc.probe import DiscInfo
from ready2rip.metadata.providers import USER_AGENT

log = logging.getLogger(__name__)

# Primary HTTPS endpoint; HTTP host kept as fallback.
CTDB_LOOKUP_URLS = (
    'https://db.cue.tools/lookup2.php',
    'http://db.cuetools.net/lookup2.php',
)

# CUETools default CTDB stride (samples are stereo frames = 4 bytes).
CTDB_STRIDE = 5880


class CtdbConfidence(Enum):
    MISMATCH = auto()
    MATCH = auto()
    NOT_IN_DB = auto()
    ERROR = auto()


@dataclass
class CtdbResult:
    track_number: int
    confidence: CtdbConfidence
    confidence_count: int = 0
    crc: str = ''
    message: str = ''


@dataclass
class CtdbEntry:
    entry_id: str
    confidence: int
    crc32: str
    track_crcs: list[int]
    toc: str = ''


@dataclass
class CtdbDatabase:
    toc: str
    entries: list[CtdbEntry] = field(default_factory=list)

    def best_match(self, track_number: int, crc: int) -> CtdbResult:
        """Sum confidence across entries whose track CRC matches *crc*."""
        if not self.entries:
            return CtdbResult(
                track_number=track_number,
                confidence=CtdbConfidence.NOT_IN_DB,
                crc=f'{crc:08x}',
                message='Disc not in CUETools Database',
            )

        idx = track_number - 1
        conf = 0
        for entry in self.entries:
            if 0 <= idx < len(entry.track_crcs) and entry.track_crcs[idx] == crc:
                conf += max(0, int(entry.confidence))

        if conf > 0:
            return CtdbResult(
                track_number=track_number,
                confidence=CtdbConfidence.MATCH,
                confidence_count=conf,
                crc=f'{crc:08x}',
                message=f'CTDB match (confidence {conf})',
            )

        max_conf = max((e.confidence for e in self.entries), default=0)
        return CtdbResult(
            track_number=track_number,
            confidence=CtdbConfidence.MISMATCH,
            confidence_count=max_conf,
            crc=f'{crc:08x}',
            message=f'No CTDB match (DB max confidence {max_conf})',
        )


def toc_string_from_info(info: DiscInfo) -> str | None:
    """Build a CTDB TOC string relative to the first audio track start.

    Format: ``0:t2:t3:…:leadout`` with data-track offsets prefixed by ``-``.
    """
    if not info.tracks:
        return None

    first = int(info.tracks[0].start_sector)
    items: list[tuple[int, int, bool]] = [
        (t.number, int(t.start_sector), True) for t in info.tracks
    ]
    for d in info.data_tracks or []:
        items.append((d.number, int(d.start_sector), False))
    items.sort(key=lambda item: item[0])

    parts: list[str] = []
    for _num, start, is_audio in items:
        rel = start - first
        parts.append(str(rel) if is_audio else f'-{abs(rel)}')

    leadout = int(info.tracks[-1].start_sector) + int(info.tracks[-1].length_sectors)
    if int(getattr(info, 'leadout_lba', 0) or 0) > leadout:
        leadout = int(info.leadout_lba)
    for d in info.data_tracks or []:
        end = int(d.start_sector) + int(d.length_sectors)
        if end > leadout:
            leadout = end
    parts.append(str(leadout - first))
    return ':'.join(parts)


def total_audio_samples(info: DiscInfo) -> int:
    """Stereo frames from INDEX 01 of track 1 through last audio lead-out."""
    return sum(int(t.length_sectors) for t in info.tracks) * 588


def laststride_samples(total_samples: int, *, stride: int = CTDB_STRIDE) -> int:
    """CUETools ``laststride`` for *total_samples* (pregap already excluded)."""
    if stride <= 0:
        raise ValueError('stride must be positive')
    return stride + (max(0, total_samples) * 2) % stride


def track_crc_bounds(
    pcm_bytes: int,
    track_number: int,
    total_tracks: int,
    total_samples: int,
    *,
    stride: int = CTDB_STRIDE,
) -> tuple[int, int]:
    """Return ``[start, end)`` byte offsets into raw PCM for a CTDB track CRC."""
    if pcm_bytes < 0 or pcm_bytes % 4 != 0:
        raise ValueError('PCM length must be a non-negative multiple of 4')
    prefix = stride // 2
    suffix = laststride_samples(total_samples, stride=stride) // 2
    start = 0
    end = pcm_bytes
    if track_number == 1 or total_tracks == 1:
        start += prefix * 4
    if track_number == total_tracks or total_tracks == 1:
        end -= suffix * 4
    if start < 0 or end > pcm_bytes or end < start:
        raise ValueError(
            f'Track too short for CTDB CRC '
            f'(need prefix={prefix} suffix={suffix} samples)'
        )
    return start, end


def compute_track_crc(
    pcm: bytes,
    track_number: int,
    total_tracks: int,
    total_samples: int,
    *,
    stride: int = CTDB_STRIDE,
) -> int:
    """CRC32 of the CTDB-truncated PCM range (IEEE / zlib)."""
    start, end = track_crc_bounds(
        len(pcm),
        track_number,
        total_tracks,
        total_samples,
        stride=stride,
    )
    return zlib.crc32(pcm[start:end]) & 0xFFFFFFFF


def load_cdda_pcm(path: Path) -> bytes:
    """Load raw interleaved LE PCM from a 16-bit stereo 44.1 kHz WAV."""
    with wave.open(str(path), 'rb') as wf:
        if wf.getnchannels() != 2 or wf.getsampwidth() != 2 or wf.getframerate() != 44100:
            raise ValueError(
                f'CTDB requires 16-bit stereo 44.1 kHz WAV, got '
                f'{wf.getnchannels()}ch {wf.getsampwidth() * 8}bit {wf.getframerate()}Hz'
            )
        return wf.readframes(wf.getnframes())


def compute_track_crc_wav(
    path: Path,
    track_number: int,
    total_tracks: int,
    total_samples: int,
    *,
    stride: int = CTDB_STRIDE,
) -> int:
    return compute_track_crc(
        load_cdda_pcm(path),
        track_number,
        total_tracks,
        total_samples,
        stride=stride,
    )


def parse_lookup_xml(raw: bytes | str, *, toc: str = '') -> CtdbDatabase:
    """Parse a ``lookup2.php`` XML body into :class:`CtdbDatabase`."""
    if isinstance(raw, bytes):
        text = raw.decode('utf-8', errors='replace')
    else:
        text = raw
    root = ET.fromstring(text)
    db = CtdbDatabase(toc=toc)
    for elem in root.iter():
        tag = elem.tag.rsplit('}', 1)[-1]
        if tag != 'entry':
            continue
        trackcrcs_raw = (elem.get('trackcrcs') or '').strip()
        if not trackcrcs_raw:
            continue
        try:
            track_crcs = [int(part, 16) for part in trackcrcs_raw.split()]
        except ValueError:
            continue
        try:
            conf = int(elem.get('confidence') or '0')
        except ValueError:
            conf = 0
        db.entries.append(
            CtdbEntry(
                entry_id=str(elem.get('id') or ''),
                confidence=conf,
                crc32=str(elem.get('crc32') or ''),
                track_crcs=track_crcs,
                toc=str(elem.get('toc') or toc),
            )
        )
    return db


def fetch_database(toc: str, timeout: float = 15.0) -> CtdbDatabase | None:
    """Download CTDB entries for *toc*. Returns empty DB when not listed."""
    from ready2rip.util import drain_http_error, is_safe_http_url, read_limited

    query = urllib.parse.urlencode(
        {
            'version': '3',
            'ctdb': '1',
            'fuzzy': '1',
            'metadata': 'none',
            'toc': toc,
        }
    )
    last_error: Exception | None = None
    for base in CTDB_LOOKUP_URLS:
        url = f'{base}?{query}'
        if not is_safe_http_url(url):
            log.warning('Refusing non-allowlisted CTDB URL: %s', url)
            continue
        request = urllib.request.Request(
            url,
            headers={'User-Agent': USER_AGENT},
            method='GET',
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                final = response.geturl()
                if final and not is_safe_http_url(final):
                    log.warning('Refusing CTDB redirect to unsafe URL: %s', final)
                    continue
                raw = read_limited(response)
            return parse_lookup_xml(raw, toc=toc)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                log.info('Disc not in CTDB: toc=%s', toc)
                drain_http_error(exc)
                return CtdbDatabase(toc=toc)
            log.warning('CTDB HTTP %s: %s', exc.code, url)
            drain_http_error(exc)
            last_error = exc
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, ET.ParseError) as exc:
            log.warning('CTDB fetch failed via %s: %s', base, exc)
            last_error = exc
    if last_error is not None:
        log.warning('CTDB lookup failed for toc=%s: %s', toc, last_error)
    return None


class CtdbVerifier:
    """Fetch CTDB once, then verify tracks as they are ripped."""

    def __init__(self) -> None:
        self._toc: str | None = None
        self._db: CtdbDatabase | None = None
        self._total_tracks = 0
        self._total_samples = 0

    def prepare(self, info: DiscInfo) -> str:
        self._toc = toc_string_from_info(info)
        self._total_tracks = info.track_count
        self._total_samples = total_audio_samples(info)
        if not self._toc:
            self._db = None
            return 'CTDB: could not build TOC'
        self._db = fetch_database(self._toc)
        if self._db is None:
            return f'CTDB: lookup failed (toc={self._toc})'
        if not self._db.entries:
            return f'CTDB: disc not in database (toc={self._toc})'
        return (
            f'CTDB: {len(self._db.entries)} entry(ies) for toc={self._toc}'
        )

    def set_totals(self, *, total_tracks: int, total_samples: int) -> None:
        if total_tracks > 0:
            self._total_tracks = total_tracks
        if total_samples > 0:
            self._total_samples = total_samples

    def verify_wav(self, path: Path, track_number: int) -> CtdbResult:
        if self._db is None:
            return CtdbResult(
                track_number=track_number,
                confidence=CtdbConfidence.ERROR,
                message='CTDB database unavailable',
            )
        if not self._db.entries:
            return CtdbResult(
                track_number=track_number,
                confidence=CtdbConfidence.NOT_IN_DB,
                message='Disc not in CUETools Database',
            )
        try:
            crc = compute_track_crc_wav(
                path,
                track_number,
                self._total_tracks,
                self._total_samples,
            )
        except Exception as exc:
            log.warning('CTDB CRC failed for track %s: %s', track_number, exc)
            return CtdbResult(
                track_number=track_number,
                confidence=CtdbConfidence.ERROR,
                message=f'CRC error: {exc}',
            )
        return self._db.best_match(track_number, crc)
