# SPDX-License-Identifier: GPL-3.0-or-later
"""AccurateRip disc IDs, CRC v1/v2, and database verification.

Algorithm and binary DB layout follow open implementations (ARver / whipper),
which implement the AccurateRip protocol described on Hydrogenaudio.
"""

from __future__ import annotations

import logging
import struct
import urllib.error
import urllib.request
import wave
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path

from ready2rip.disc.probe import DiscInfo
from ready2rip.metadata.providers import USER_AGENT

log = logging.getLogger(__name__)

AR_BASE = 'http://www.accuraterip.com/accuraterip/'
# Samples (stereo frames) to skip at start of first / end of last track
_SKIP_FRAMES = 5 * 588  # 5 sectors × 588 samples/sector


class AccurateRipConfidence(Enum):
    MISMATCH = auto()
    MATCH = auto()
    NOT_IN_DB = auto()
    ERROR = auto()


@dataclass
class AccurateRipResult:
    track_number: int
    confidence: AccurateRipConfidence
    confidence_count: int = 0
    crc_v1: str = ''
    crc_v2: str = ''
    matched_version: str = ''  # 'v1' | 'v2' | ''
    message: str = ''


@dataclass
class AccurateRipDiscIds:
    """AccurateRip + FreeDB identifiers used in the dBAR URL."""

    num_tracks: int
    ar_id1: str
    ar_id2: str
    freedb_id: str

    @property
    def disc_id_string(self) -> str:
        return f'0{self.num_tracks:02d}-{self.ar_id1}-{self.ar_id2}-{self.freedb_id}'


@dataclass
class AccurateRipDatabase:
    """Parsed AccurateRip binary response(s) for one disc."""

    # track_number (1-based) -> list of (checksum, confidence)
    by_track: dict[int, list[tuple[int, int]]] = field(default_factory=dict)
    response_count: int = 0

    def best_match(self, track_number: int, crc_v1: int, crc_v2: int) -> AccurateRipResult:
        entries = self.by_track.get(track_number) or []
        if not entries:
            return AccurateRipResult(
                track_number=track_number,
                confidence=AccurateRipConfidence.NOT_IN_DB,
                crc_v1=f'{crc_v1:08x}',
                crc_v2=f'{crc_v2:08x}',
                message='Track not in AccurateRip database',
            )

        best_conf = 0
        matched_ver = ''
        for checksum, conf in entries:
            if checksum == crc_v1 and conf >= best_conf:
                best_conf = conf
                matched_ver = 'v1'
            if checksum == crc_v2 and conf >= best_conf:
                best_conf = conf
                matched_ver = 'v2'

        if matched_ver:
            return AccurateRipResult(
                track_number=track_number,
                confidence=AccurateRipConfidence.MATCH,
                confidence_count=best_conf,
                crc_v1=f'{crc_v1:08x}',
                crc_v2=f'{crc_v2:08x}',
                matched_version=matched_ver,
                message=f'AccurateRip {matched_ver} match (confidence {best_conf})',
            )

        max_conf = max(c for _s, c in entries)
        return AccurateRipResult(
            track_number=track_number,
            confidence=AccurateRipConfidence.MISMATCH,
            confidence_count=max_conf,
            crc_v1=f'{crc_v1:08x}',
            crc_v2=f'{crc_v2:08x}',
            message=f'No AccurateRip match (DB max confidence {max_conf})',
        )


def disc_ids_from_info(info: DiscInfo) -> AccurateRipDiscIds | None:
    """Compute AccurateRip disc IDs from a probed TOC (cdparanoia LBA)."""
    if not info.tracks:
        return None

    # MusicBrainz/AR style: LBA + 150 pregap frames for absolute offsets.
    pregap = 150
    audio_offsets_lba = [t.start_sector + pregap for t in info.tracks]
    last = info.tracks[-1]
    leadout_lba = last.start_sector + last.length_sectors + pregap

    # Convert to LSN (absolute - 150) for AR ID formula.
    lsn_offsets = [o - pregap for o in audio_offsets_lba]
    lsn_leadout = leadout_lba - pregap

    id1 = 0
    id2 = 0
    for track_num, offset in enumerate(lsn_offsets, start=1):
        id1 += offset
        id2 += (offset or 1) * track_num
    id1 += lsn_leadout
    id2 += lsn_leadout * (len(lsn_offsets) + 1)
    id1 &= 0xFFFFFFFF
    id2 &= 0xFFFFFFFF

    freedb = info.freedb_id or _freedb_from_lba(audio_offsets_lba, leadout_lba)
    if not freedb:
        return None

    return AccurateRipDiscIds(
        num_tracks=len(info.tracks),
        ar_id1=f'{id1:08x}',
        ar_id2=f'{id2:08x}',
        freedb_id=freedb.lower(),
    )


def _freedb_from_lba(offsets_lba: list[int], leadout_lba: int) -> str | None:
    if not offsets_lba:
        return None

    def sum_digits(n: int) -> int:
        total = 0
        while n > 0:
            total += n % 10
            n //= 10
        return total

    n = 0
    for off in offsets_lba:
        n += sum_digits(off // 75)
    t = (leadout_lba // 75) - (offsets_lba[0] // 75)
    disc_id = ((n % 0xFF) << 24) | (t << 8) | len(offsets_lba)
    return f'{disc_id:08x}'


def fetch_database(ids: AccurateRipDiscIds, timeout: float = 15.0) -> AccurateRipDatabase | None:
    """Download and parse AccurateRip binary data for *ids*."""
    dir_ = f'{ids.ar_id1[-1]}/{ids.ar_id1[-2]}/{ids.ar_id1[-3]}/'
    file_ = (
        f'dBAR-0{ids.num_tracks:02d}-'
        f'{ids.ar_id1}-{ids.ar_id2}-{ids.freedb_id}.bin'
    )
    url = AR_BASE + dir_ + file_
    from ready2rip.util import is_safe_http_url, read_limited

    if not is_safe_http_url(url):
        log.warning('Refusing non-http(s) AccurateRip URL: %s', url)
        return None
    request = urllib.request.Request(
        url,
        headers={'User-Agent': USER_AGENT},
        method='GET',
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            final = response.geturl()
            if final and not is_safe_http_url(final):
                log.warning('Refusing AccurateRip redirect to unsafe URL: %s', final)
                return None
            raw = read_limited(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            log.info('Disc not in AccurateRip database: %s', ids.disc_id_string)
        else:
            log.warning('AccurateRip HTTP %s: %s', exc.code, url)
        try:
            exc.read()
            exc.close()
        except Exception:  # noqa: BLE001
            pass
        return None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        log.warning('AccurateRip fetch failed: %s', exc)
        return None

    try:
        return _parse_dbar(raw, ids)
    except (struct.error, ValueError) as exc:
        log.warning('AccurateRip parse failed: %s', exc)
        return None


def _parse_dbar(raw: bytes, ids: AccurateRipDiscIds) -> AccurateRipDatabase:
    db = AccurateRipDatabase()
    offset = 0
    expected_tracks = ids.num_tracks

    while offset < len(raw):
        if offset + 13 > len(raw):
            raise ValueError('Truncated AccurateRip header')
        num_tracks, ar1, ar2, freedb = struct.unpack_from('<BLLL', raw, offset)
        offset += 13
        if num_tracks != expected_tracks:
            raise ValueError(
                f'Unexpected track count in AR header: {num_tracks} != {expected_tracks}'
            )
        if f'{ar1:08x}' != ids.ar_id1 or f'{ar2:08x}' != ids.ar_id2:
            raise ValueError('AccurateRip header disc ID mismatch')

        db.response_count += 1
        for track_i in range(num_tracks):
            if offset + 9 > len(raw):
                raise ValueError('Truncated AccurateRip track entry')
            conf, checksum, _checksum_450 = struct.unpack_from('<BLL', raw, offset)
            offset += 9
            track_no = track_i + 1
            if conf == 0:
                continue
            db.by_track.setdefault(track_no, []).append((checksum, conf))

    return db


def load_cdda_wav_samples(path: Path) -> memoryview:
    """Load a CDDA WAV as a memoryview of interleaved LE uint32 stereo frames."""
    with wave.open(str(path), 'rb') as wf:
        if wf.getnchannels() != 2 or wf.getsampwidth() != 2 or wf.getframerate() != 44100:
            raise ValueError(
                f'AccurateRip requires 16-bit stereo 44.1 kHz WAV, got '
                f'{wf.getnchannels()}ch {wf.getsampwidth() * 8}bit {wf.getframerate()}Hz'
            )
        raw = wf.readframes(wf.getnframes())
    if len(raw) % 4 != 0:
        raw = raw[: len(raw) - (len(raw) % 4)]
    return memoryview(raw).cast('I')  # unsigned 32-bit LE words


def compute_checksums_wav(
    path: Path,
    track_number: int,
    total_tracks: int,
    *,
    sample_offset: int = 0,
) -> tuple[int, int]:
    """Return (crc_v1, crc_v2) for a CDDA WAV file."""
    samples = load_cdda_wav_samples(path)
    return compute_checksums_samples(
        samples,
        track_number,
        total_tracks,
        sample_offset=sample_offset,
    )


def compute_checksums_samples(
    samples: memoryview,
    track_number: int,
    total_tracks: int,
    *,
    sample_offset: int = 0,
) -> tuple[int, int]:
    """Compute AccurateRip v1/v2 over a sequence of uint32 stereo frames.

    *sample_offset* is the drive read offset in samples (stereo frames).
    Positive offset discards samples from the start (typical for many drives).
    """
    n = len(samples)
    if n < _SKIP_FRAMES * 2:
        raise ValueError('Audio too short for AccurateRip')

    # Match whipper/accuraterip-checksum.c: MulBy starts at 1; skip window
    # uses 0-based bounds then compares to MulBy.
    check_from = 0
    check_to = n
    if track_number == 1:
        check_from += _SKIP_FRAMES
    if track_number == total_tracks:
        check_to -= _SKIP_FRAMES

    csum_hi = 0
    csum_lo = 0

    # Index into samples with offset: logical sample i (0-based) comes from
    # physical index i + sample_offset; out-of-range → silence (0).
    for i in range(n):
        mul = i + 1
        if mul < check_from or mul > check_to:
            continue
        src = i + sample_offset
        word = samples[src] if 0 <= src < n else 0
        product = int(word) * mul
        csum_hi = (csum_hi + (product >> 32)) & 0xFFFFFFFF
        csum_lo = (csum_lo + (product & 0xFFFFFFFF)) & 0xFFFFFFFF

    v1 = csum_lo
    v2 = (csum_lo + csum_hi) & 0xFFFFFFFF
    return v1, v2


# Most common AccurateRip drive offsets first (samples), ordered by how often
# they appear in the public drive-offset database / real-world rips.
# Calibration tries these before any wider search.
POPULAR_OFFSETS = (
    # Very common consumer / notebook drives
    6, 48, 102, 667, 0, 12, 18, 30, 98, 116, 594, 618, 676, 679, 685, 691,
    704, 738, 784, 103, 86, 87, 91, 97, 99, 100, 105, 108, 112, 120, 126,
    129, 138, 150, 182, 234, 258, 294, 318, 366, 390, 438, 462, 564,
    690, 696, 702, 723, 739, 740, 742, 772, 810, 855, 984, 1020, 1035,
    1104, 1162, 1176, 1194, 1290, 1332, 1380, 1488, 1506, 1548, 1674,
    24, 54, 66, 72, 84, 96,
    # Common negative offsets
    -6, -12, -24, -48, -54, -72, -96, -116, -147, -192, -270, -376, -436,
    -488, -540, -589, -647, -667, -685, -697, -738, -769, -784, -889, -968,
    -984, -992, -1044, -1164, -1303, -1473,
)

class AccurateRipVerifier:
    """Fetch DB once, then verify tracks as they are ripped."""

    def __init__(self, sample_offset: int = 0) -> None:
        self.sample_offset = sample_offset
        self._ids: AccurateRipDiscIds | None = None
        self._db: AccurateRipDatabase | None = None
        self._total_tracks = 0

    def prepare(self, info: DiscInfo) -> str:
        """Compute IDs and fetch the database. Returns a short status string."""
        self._ids = disc_ids_from_info(info)
        self._total_tracks = info.track_count
        if self._ids is None:
            self._db = None
            return 'AccurateRip: could not compute disc IDs'
        self._db = fetch_database(self._ids)
        if self._db is None:
            return f'AccurateRip: disc not in database ({self._ids.disc_id_string})'
        return (
            f'AccurateRip: {self._db.response_count} response(s) '
            f'for {self._ids.disc_id_string}'
        )

    def set_total_tracks(self, total: int) -> None:
        """Override disc track count used for first/last AR sample skip rules."""
        if total > 0:
            self._total_tracks = total

    def verify_wav(self, path: Path, track_number: int) -> AccurateRipResult:
        if self._db is None:
            return AccurateRipResult(
                track_number=track_number,
                confidence=AccurateRipConfidence.NOT_IN_DB,
                message='AccurateRip database unavailable',
            )
        try:
            v1, v2 = compute_checksums_wav(
                path,
                track_number,
                self._total_tracks,
                sample_offset=self.sample_offset,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning('AccurateRip CRC failed for track %s: %s', track_number, exc)
            return AccurateRipResult(
                track_number=track_number,
                confidence=AccurateRipConfidence.ERROR,
                message=f'CRC error: {exc}',
            )
        result = self._db.best_match(track_number, v1, v2)
        if (
            result.confidence == AccurateRipConfidence.MISMATCH
            and self.sample_offset == 0
        ):
            result.message = (
                f'{result.message} · try setting Drive sample offset in Rip options '
                f'(accuraterip.com/driveoffsets.htm)'
            )
        return result