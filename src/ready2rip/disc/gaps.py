# SPDX-License-Identifier: GPL-3.0-or-later
"""INDEX 00 pregap detection (cdrdao, then Q-subchannel binary search)."""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path

from ready2rip.disc.mmc import MmcDevice
from ready2rip.disc.probe import DataTrackInfo, DiscInfo, TrackInfo

log = logging.getLogger(__name__)

_TRACK_RE = re.compile(
    r'^TRACK\s+(AUDIO|MODE1\S*|MODE2\S*|MODE1|MODE2)\b',
    re.IGNORECASE,
)
_START_RE = re.compile(r'^START\s+(\d+):(\d+):(\d+)\s*$')
_INDEX_RE = re.compile(r'^INDEX\s+(\d+)\s+(\d+):(\d+):(\d+)\s*$')
_Q_RETRIES = 3


@dataclass(frozen=True)
class ParsedGap:
    """One TRACK from a cdrdao TOC (1-based number in file order)."""

    number: int
    pregap_sectors: int
    is_data: bool


def enrich_pregaps(device: str, disc: DiscInfo) -> str:
    """Fill INDEX 00 lengths on *disc* tracks. Returns a short source label.

    Prefers ``cdrdao read-toc`` when installed; otherwise binary-searches
    Q-subchannel INDEX. Track 1 always keeps the TOC pause (``start_sector``).
    Detection failure is non-fatal: existing TOC track-1 pregap is kept.
    """
    if not disc.tracks:
        disc.pregap_source = 'none'
        return disc.pregap_source

    _apply_track1_toc_pregap(disc)

    parsed = _pregaps_from_cdrdao(device)
    if parsed:
        _apply_parsed_gaps(disc, parsed)
        disc.pregap_source = 'cdrdao'
        log.info('Pregaps from cdrdao (%d TRACK entries)', len(parsed))
        return disc.pregap_source

    scanned = _pregaps_from_q(device, disc)
    if scanned:
        disc.pregap_source = 'q-subchannel'
        log.info('Pregaps from Q-subchannel (%d audio tracks)', len(disc.tracks))
        return disc.pregap_source

    disc.pregap_source = 'toc-track1' if disc.tracks[0].pregap_sectors else 'none'
    return disc.pregap_source


def parse_cdrdao_toc(text: str) -> list[ParsedGap]:
    """Parse cdrdao TOC text into per-TRACK pregaps (START / INDEX 00)."""
    gaps: list[ParsedGap] = []
    number = 0
    is_data = False
    start_sectors = 0
    index00: int | None = None
    index01: int | None = None
    saw_track = False

    def flush() -> None:
        nonlocal saw_track, start_sectors, index00, index01, number, is_data
        if not saw_track:
            return
        pregap = start_sectors
        if index00 is not None and index01 is not None and index01 >= index00:
            pregap = index01 - index00
        gaps.append(
            ParsedGap(number=number, pregap_sectors=max(0, pregap), is_data=is_data)
        )
        start_sectors = 0
        index00 = None
        index01 = None

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith('//'):
            continue
        track_match = _TRACK_RE.match(line)
        if track_match:
            flush()
            number += 1
            kind = track_match.group(1).upper()
            is_data = not kind.startswith('AUDIO')
            saw_track = True
            continue
        start_match = _START_RE.match(line)
        if start_match:
            start_sectors = _msf_to_sectors(
                int(start_match.group(1)),
                int(start_match.group(2)),
                int(start_match.group(3)),
            )
            continue
        index_match = _INDEX_RE.match(line)
        if index_match:
            idx = int(index_match.group(1))
            sectors = _msf_to_sectors(
                int(index_match.group(2)),
                int(index_match.group(3)),
                int(index_match.group(4)),
            )
            if idx == 0:
                index00 = sectors
            elif idx == 1:
                index01 = sectors
    flush()
    return gaps


def _pregaps_from_cdrdao(device: str) -> list[ParsedGap] | None:
    binary = shutil.which('cdrdao')
    if not binary:
        return None
    try:
        with tempfile.TemporaryDirectory(prefix='ready2rip-toc-') as tmp:
            toc_path = Path(tmp) / 'disc.toc'
            completed = subprocess.run(
                [binary, 'read-toc', '--device', device, str(toc_path)],
                check=False,
                capture_output=True,
                text=True,
                timeout=120,
                start_new_session=True,
            )
            if not toc_path.is_file():
                log.debug(
                    'cdrdao read-toc produced no file (rc=%s): %s',
                    completed.returncode,
                    (completed.stderr or completed.stdout or '')[:300],
                )
                return None
            parsed = parse_cdrdao_toc(toc_path.read_text(encoding='utf-8', errors='replace'))
            return parsed or None
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.debug('cdrdao read-toc failed: %s', exc)
        return None


def _pregaps_from_q(device: str, disc: DiscInfo) -> bool:
    """Binary-search Q INDEX 00 for tracks 2+. Mutates *disc.tracks*."""
    tracks = list(disc.tracks)
    if len(tracks) < 2:
        return False
    try:
        mmc = MmcDevice(device)
    except OSError as exc:
        log.debug('Q-pregap open %s failed: %s', device, exc)
        return False
    changed = False
    try:
        updated: list[TrackInfo] = [tracks[0]]
        for i in range(1, len(tracks)):
            prev = tracks[i - 1]
            cur = tracks[i]
            lo = int(prev.start_sector)
            hi = int(cur.start_sector)
            pregap = _first_index00_length(mmc, lo, hi)
            if pregap != cur.pregap_sectors:
                cur = replace(cur, pregap_sectors=pregap)
                changed = True
            updated.append(cur)
        disc.tracks = updated
    finally:
        mmc.close()
    return changed or any(t.pregap_sectors for t in disc.tracks[1:])


def _first_index00_length(mmc: MmcDevice, lo: int, hi: int) -> int:
    """Sectors of INDEX 00 ending at *hi* (exclusive INDEX 01)."""
    if hi <= lo + 1:
        return 0
    tail = _q_index(mmc, hi - 1)
    if tail != 0:
        return 0
    left, right = lo, hi - 1
    first0 = hi - 1
    while left <= right:
        mid = (left + right) // 2
        q = _q_index(mmc, mid)
        if q == 0:
            first0 = mid
            right = mid - 1
        else:
            # None or INDEX 01 of the previous track — search right.
            left = mid + 1
    return max(0, hi - first0)


def _q_index(mmc: MmcDevice, lba: int) -> int | None:
    last: int | None = None
    for _ in range(_Q_RETRIES):
        last = mmc.read_q_index(lba)
        if last is not None:
            return last
    return last


def _apply_track1_toc_pregap(disc: DiscInfo) -> None:
    t0 = disc.tracks[0]
    if t0.number != 1:
        return
    pregap = max(0, int(t0.start_sector))
    if t0.pregap_sectors == pregap:
        return
    disc.tracks[0] = replace(t0, pregap_sectors=pregap)


def _apply_parsed_gaps(disc: DiscInfo, parsed: list[ParsedGap]) -> None:
    by_number = {g.number: g for g in parsed}
    audio = [g for g in parsed if not g.is_data]
    new_audio: list[TrackInfo] = []
    for i, track in enumerate(disc.tracks):
        gap = by_number.get(track.number)
        if gap is None and i < len(audio):
            gap = audio[i]
        if gap is None:
            new_audio.append(track)
            continue
        new_audio.append(replace(track, pregap_sectors=max(0, gap.pregap_sectors)))
    disc.tracks = new_audio

    new_data: list[DataTrackInfo] = []
    for d in disc.data_tracks:
        gap = by_number.get(d.number)
        if gap is None:
            new_data.append(d)
            continue
        new_data.append(
            DataTrackInfo(
                number=d.number,
                start_sector=d.start_sector,
                length_sectors=d.length_sectors,
                mode=d.mode,
                session=d.session,
                pregap_sectors=max(0, gap.pregap_sectors),
            )
        )
    disc.data_tracks = new_data


def _msf_to_sectors(minutes: int, seconds: int, frames: int) -> int:
    return (minutes * 60 + seconds) * 75 + frames
