# SPDX-License-Identifier: GPL-3.0-or-later
"""Drive audio-cache detection and defeat (whipper / cyanrip style)."""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
import time
import wave
import zlib
from dataclasses import dataclass
from pathlib import Path

from ready2rip.disc.probe import DiscInfo

log = logging.getLogger(__name__)

# Short segment used for cache probes (1 second of CDDA).
_PROBE_SECTORS = 75


@dataclass
class DriveCacheResult:
    """Result of an audio-cache analysis pass."""

    caches: bool | None  # True / False / None (unknown / skipped)
    message: str
    first_ms: float = 0.0
    second_ms: float = 0.0
    crc_match: bool | None = None


def detect_drive_cache(
    device: str,
    disc: DiscInfo | None = None,
    *,
    timeout: int = 120,
) -> DriveCacheResult:
    """Detect whether *device* returns cached audio on re-reads.

    Method (inspired by whipper / cdparanoia analyze):
    1. Extract a short sector range twice in succession.
    2. Compare PCM CRC and wall-clock times.
    3. If both CRCs match and the second read is much faster, the drive
       almost certainly serves from cache.
    """
    if shutil.which('cdparanoia') is None:
        return DriveCacheResult(
            caches=None,
            message='cdparanoia not found; cache detection skipped',
        )

    span = _probe_span(disc)
    if span is None:
        return DriveCacheResult(
            caches=None,
            message='No usable sector range for cache detection',
        )

    with tempfile.TemporaryDirectory(prefix='ready2rip-cache-') as tmp:
        tmp_path = Path(tmp)
        a = tmp_path / 'a.wav'
        b = tmp_path / 'b.wav'
        try:
            t0 = time.monotonic()
            _extract_span(device, span, a, timeout=timeout)
            first_ms = (time.monotonic() - t0) * 1000.0
            crc_a = _pcm_crc(a)

            t1 = time.monotonic()
            _extract_span(device, span, b, timeout=timeout)
            second_ms = (time.monotonic() - t1) * 1000.0
            crc_b = _pcm_crc(b)
        except Exception as exc:  # noqa: BLE001
            log.warning('Drive cache detection failed: %s', exc)
            return DriveCacheResult(
                caches=None,
                message=f'Cache detection failed: {exc}',
            )

    match = crc_a is not None and crc_a == crc_b and crc_a != 0
    # Second pass much faster with identical data ⇒ audio cache.
    caches = False
    if match and first_ms > 80.0 and second_ms < first_ms * 0.35 and second_ms < 400.0:
        caches = True
        message = (
            f'Drive caches audio (re-read {second_ms:.0f} ms vs '
            f'{first_ms:.0f} ms, CRC match)'
        )
    elif match:
        message = (
            f'No clear audio cache (re-read {second_ms:.0f} ms vs '
            f'{first_ms:.0f} ms, CRC match)'
        )
        caches = False
    else:
        message = (
            f'Cache probe CRCs differ (unstable read or no cache); '
            f'times {first_ms:.0f}/{second_ms:.0f} ms'
        )
        caches = False

    log.info('Drive cache: %s', message)
    return DriveCacheResult(
        caches=caches,
        message=message,
        first_ms=first_ms,
        second_ms=second_ms,
        crc_match=match,
    )


def flush_drive_cache(
    device: str,
    disc: DiscInfo | None,
    *,
    timeout: int = 60,
) -> None:
    """Force the drive to read a distant sector range to defeat audio cache.

    Reads a short span from the last track (or end of disc) and discards it.
    Safe to call even when cache was not detected — cheap insurance for
    test & copy.
    """
    if shutil.which('cdparanoia') is None:
        return

    span = _flush_span(disc)
    if span is None:
        return

    with tempfile.TemporaryDirectory(prefix='ready2rip-flush-') as tmp:
        out = Path(tmp) / 'flush.wav'
        try:
            _extract_span(device, span, out, timeout=timeout, quiet=True)
        except Exception as exc:  # noqa: BLE001
            log.debug('Cache flush read failed (non-fatal): %s', exc)


def _probe_span(disc: DiscInfo | None) -> str | None:
    """Pick a short absolute sector range near the start of track 1."""
    if disc is None or not disc.tracks:
        # Absolute sectors 0–75 (1 s) — works on most audio discs.
        return f'[.0]-[.{_PROBE_SECTORS}]'

    t1 = disc.tracks[0]
    start = max(0, t1.start_sector)
    # Prefer mid-track to avoid lead-in quirks.
    mid = start + max(0, min(t1.length_sectors // 2, t1.length_sectors - _PROBE_SECTORS - 1))
    if t1.length_sectors < _PROBE_SECTORS + 2:
        end = start + max(1, t1.length_sectors - 1)
        if end <= start:
            end = start + _PROBE_SECTORS
        return f'[.{start}]-[.{end}]'
    return f'[.{mid}]-[.{mid + _PROBE_SECTORS}]'


def _flush_span(disc: DiscInfo | None) -> str | None:
    """Span far from the usual probe region (end of last track)."""
    if disc is None or not disc.tracks:
        return f'[.0]-[.{_PROBE_SECTORS}]'

    last = disc.tracks[-1]
    end = last.start_sector + last.length_sectors
    start = max(last.start_sector, end - _PROBE_SECTORS)
    if start >= end:
        start = max(0, end - _PROBE_SECTORS)
        end = start + _PROBE_SECTORS
    return f'[.{start}]-[.{end}]'


def _extract_span(
    device: str,
    span: str,
    wav_path: Path,
    *,
    timeout: int,
    quiet: bool = True,
) -> None:
    cmd = [
        'cdparanoia',
        '-w',
        '-d',
        device,
        # Single token — "-z" "20" is misread as track 20 by cdparanoia.
        '--never-skip=20',
    ]
    if quiet:
        cmd.append('-q')
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
        if len(detail) > 200:
            detail = detail[:200] + '…'
        raise RuntimeError(detail or f'cdparanoia span failed ({span})')


def _pcm_crc(wav_path: Path) -> int | None:
    try:
        with wave.open(str(wav_path), 'rb') as wf:
            crc = 0
            remaining = wf.getnframes()
            while remaining > 0:
                take = min(65536, remaining)
                data = wf.readframes(take)
                remaining -= take
                crc = zlib.crc32(data, crc)
            return crc & 0xFFFFFFFF
    except Exception:  # noqa: BLE001
        try:
            raw = wav_path.read_bytes()
            pcm = raw[44:] if len(raw) > 44 and raw[:4] == b'RIFF' else raw
            return zlib.crc32(pcm) & 0xFFFFFFFF
        except OSError:
            return None
