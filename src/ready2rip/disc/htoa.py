# SPDX-License-Identifier: GPL-3.0-or-later
"""Hidden Track One Audio (HTOA) detection and extraction."""

from __future__ import annotations

import logging
import shutil
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path

from ready2rip.disc.probe import DiscInfo

log = logging.getLogger(__name__)

# Ignore classic 2-second digital silence pregaps under this length unless
# the user forces a check — whipper still extracts and tests silence.
_MIN_INTERESTING_SECTORS = 1  # any positive pregap is a candidate


@dataclass
class HtoaInfo:
    """Pregap before track 1 (possible HTOA)."""

    start_sector: int  # always 0 for absolute disc start
    length_sectors: int
    is_silent: bool | None = None  # None until audio is analyzed
    message: str = ''

    @property
    def duration_seconds(self) -> float:
        return self.length_sectors / 75.0

    @property
    def duration_label(self) -> str:
        total = int(round(self.duration_seconds))
        minutes, seconds = divmod(total, 60)
        return f'{minutes}:{seconds:02d}'


def detect_htoa(disc: DiscInfo | None) -> HtoaInfo | None:
    """Return HTOA candidate if track 1 has a pregap (start_sector > 0).

    On a normal Red Book disc the first track often starts at LBA 0 after
    the 150-sector lead-in is accounted for by the drive. When the TOC
    reports a positive start for track 1, sectors ``[0, start)`` are the
    hidden pregap that may contain real audio (HTOA).
    """
    if disc is None or not disc.tracks:
        return None

    t1 = disc.tracks[0]
    if t1.number != 1:
        # Still use the first listed track if numbering is odd.
        pass

    pregap = t1.start_sector
    if pregap < _MIN_INTERESTING_SECTORS:
        return None

    info = HtoaInfo(
        start_sector=0,
        length_sectors=pregap,
        message=(
            f'Pregap before track 1: {pregap} sectors '
            f'({pregap / 75.0:.2f}s) — checking for HTOA'
        ),
    )
    log.info('%s', info.message)
    return info


def is_digitally_silent(wav_path: Path, *, threshold: int = 0) -> bool:
    """True if every PCM sample is within *threshold* of zero."""
    try:
        with wave.open(str(wav_path), 'rb') as wf:
            if wf.getsampwidth() != 2:
                # Non-CDDA width: treat as non-silent if any non-zero byte.
                data = wf.readframes(wf.getnframes())
                return not any(data)
            remaining = wf.getnframes()
            while remaining > 0:
                take = min(65536, remaining)
                data = wf.readframes(take)
                remaining -= take
                for i in range(0, len(data) - 1, 2):
                    sample = int.from_bytes(data[i : i + 2], 'little', signed=True)
                    if abs(sample) > threshold:
                        return False
            return True
    except Exception as exc:  # noqa: BLE001
        log.warning('HTOA silence check failed for %s: %s', wav_path, exc)
        return False


def extract_htoa(
    device: str,
    htoa: HtoaInfo,
    wav_path: Path,
    *,
    timeout: int = 600,
    mode: str = 'secure',
) -> None:
    """Extract the pregap range before track 1 into *wav_path*."""
    from ready2rip.util import find_cdparanoia

    binary = find_cdparanoia()
    if not binary:
        raise RuntimeError('cdparanoia not found')

    end = htoa.length_sectors
    if end <= 0:
        raise RuntimeError('HTOA length is zero')

    # Absolute sector span: from disc start through pregap end (exclusive end
    # is expressed as the start of track 1 in TOC terms).
    span = f'[.0]-[.{end}]'
    cmd = [
        binary,
        '-w',
        '-d',
        device,
        '-q',
    ]
    if mode == 'burst':
        cmd.append('-Z')
    else:
        # Single token; "-z" "40" is misread as track 40.
        cmd.append('--never-skip=40')
    cmd.extend([span, str(wav_path)])

    completed = subprocess.run(
        cmd,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if completed.returncode != 0 or not wav_path.is_file() or wav_path.stat().st_size < 1000:
        detail = (completed.stderr or completed.stdout or '').strip()
        if len(detail) > 300:
            detail = detail[:300] + '…'
        raise RuntimeError(detail or f'HTOA extract failed ({span})')


def htoa_display_title() -> str:
    return 'Hidden Track One Audio'
