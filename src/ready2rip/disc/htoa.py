# SPDX-License-Identifier: GPL-3.0-or-later
"""Hidden Track One Audio (HTOA) / track-1 pregap handling (EAC-style).

Exact Audio Copy behaviour we mirror:

* **Track 1 is never extended** with the pregap. When track 1 is ripped,
  extraction starts at TOC index 01 (the track's listed start sector). The
  INDEX 00 region before that is not appended onto track 1.
* A **standard 2-second** (150 sector) pause before track 1 is normal Red Book
  structure and is **ignored** for HTOA purposes.
* Only when the pregap is **longer than 2 seconds** do we treat sectors
  ``[0, track1_start)`` as a possible hidden track. Digitally silent audio is
  discarded; non-silent audio is saved as track 00 (HTOA).
"""

from __future__ import annotations

import logging
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path

from ready2rip.disc.probe import DiscInfo

log = logging.getLogger(__name__)

# Red Book: 2 seconds × 75 sectors/s = 150 sectors — normal track-1 pause.
STANDARD_TRACK1_PREGAP_SECTORS = 150


@dataclass
class HtoaInfo:
    """Pregap before track 1 (possible HTOA). Never part of the track 1 file."""

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
    """Return an HTOA candidate using EAC-style track-1 pregap rules.

    * ``pregap <= 150`` sectors → ignore (standard pause; not a hidden track).
    * ``pregap > 150`` → candidate for extraction as track 00; silence is
      decided after a trial rip.

    Track 1 itself is always left starting at its TOC start (index 01).
    """
    if disc is None or not disc.tracks:
        return None

    t1 = disc.tracks[0]
    pregap = int(t1.start_sector)
    if pregap <= 0:
        log.debug('Track 1 starts at sector 0 — no INDEX 00 pregap')
        return None

    if pregap <= STANDARD_TRACK1_PREGAP_SECTORS:
        log.info(
            'Track 1 pregap is %s sectors (≤%ss standard) — ignored for HTOA '
            '(EAC-style; track 1 is not extended with the pause)',
            pregap,
            STANDARD_TRACK1_PREGAP_SECTORS // 75,
        )
        return None

    extra = pregap - STANDARD_TRACK1_PREGAP_SECTORS
    info = HtoaInfo(
        start_sector=0,
        length_sectors=pregap,
        message=(
            f'Extended pregap before track 1: {pregap} sectors '
            f'({pregap / 75.0:.2f}s, {extra} sectors past the standard 2s) — '
            f'checking for HTOA (track 1 itself starts at sector {t1.start_sector})'
        ),
    )
    log.info('%s', info.message)
    return info


def is_digitally_silent(wav_path: Path, *, threshold: int = 0) -> bool:
    """True if every PCM sample is within *threshold* of zero."""
    try:
        with wave.open(str(wav_path), 'rb') as wf:
            if wf.getsampwidth() != 2:
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
    sample_offset: int = 0,
) -> None:
    """Extract the pregap range before track 1 into *wav_path*.

    Uses an absolute sector span so track 1's own rip (by track number) never
    includes this region — same separation as EAC HTOA vs track 01.
    """
    from ready2rip.util import find_cdparanoia

    binary = find_cdparanoia()
    if not binary:
        raise RuntimeError('cdparanoia not found')

    end = htoa.length_sectors
    if end <= 0:
        raise RuntimeError('HTOA length is zero')

    # Absolute span [0, track1_start). libcdio-paranoia: [.A]-[.B] end is
    # relative (last inclusive = A+B), so B = end - 1 for exclusive end.
    span = f'[.0]-[.{end - 1}]'
    cmd = [
        binary,
        '-w',
        '-d',
        device,
    ]
    if sample_offset:
        cmd.extend(['-O', str(int(sample_offset))])
    if mode == 'burst':
        cmd.extend(['-Z', '-q'])
    else:
        # Single token; "-z" "200" is misread as track 200.
        cmd.extend(['--never-skip=200', '-X', '-e'])
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
        # Drop dense progress lines for the error message.
        diag = [
            ln
            for ln in detail.splitlines()
            if ln.strip() and not ln.strip().startswith('##:')
        ]
        detail = ' | '.join(diag[-6:]) if diag else detail
        if len(detail) > 300:
            detail = detail[:300] + '…'
        raise RuntimeError(detail or f'HTOA extract failed ({span})')


def htoa_display_title() -> str:
    return 'Hidden Track One Audio'
