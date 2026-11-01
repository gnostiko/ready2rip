# SPDX-License-Identifier: GPL-3.0-or-later
"""Drive audio-cache defeat (seek/read away between test and copy)."""

from __future__ import annotations

import logging
import subprocess
import tempfile
import time
from pathlib import Path

from ready2rip.disc.probe import DiscInfo

log = logging.getLogger(__name__)

# Short segment used when flushing via a burst audio read (~2 s of CDDA).
_PROBE_SECTORS = 150


def flush_drive_cache(
    device: str,
    disc: DiscInfo | None,
    *,
    timeout: int = 60,
    avoid_start: int | None = None,
    avoid_end: int | None = None,
) -> bool:
    """Force the drive to read a distant sector range to defeat audio cache.

    Prefer a span that does **not** overlap ``[avoid_start, avoid_end)`` — the
    region just extracted — so last-track and Copy Image flushes actually seek
    away from cached audio. When no clean audio span exists (full-disc image),
    try a data-track READ CD outside the avoid window, then SCSI SEEK(10).
    Returns True when a non-overlapping eviction succeeded.
    """
    from ready2rip.util import find_cdparanoia

    span, clean = _flush_span(disc, avoid_start=avoid_start, avoid_end=avoid_end)

    if clean and span is not None and find_cdparanoia() is not None:
        with tempfile.TemporaryDirectory(prefix='ready2rip-flush-') as tmp:
            out = Path(tmp) / 'flush.wav'
            try:
                # Burst seek+read is enough to evict cache and is much faster than
                # a paranoia pass whose audio we immediately discard.
                _extract_span(
                    device, span, out, timeout=timeout, quiet=True, burst=True
                )
                return True
            except Exception as exc:
                log.debug('Cache flush audio read failed: %s', exc)

    # Full-disc / HTOA image: no audio outside the avoid window.
    if _flush_via_data_or_seek(device, disc, avoid_start, avoid_end):
        return True

    if span is not None and find_cdparanoia() is not None:
        log.warning(
            'Cache flush span %s overlaps the just-read region; '
            'defeat may be weaker (Copy Image / full-disc avoid)',
            span,
        )
        with tempfile.TemporaryDirectory(prefix='ready2rip-flush-') as tmp:
            out = Path(tmp) / 'flush.wav'
            try:
                _extract_span(
                    device, span, out, timeout=timeout, quiet=True, burst=True
                )
            except Exception as exc:
                log.debug('Cache flush read failed (non-fatal): %s', exc)
                return False
    return False


def _flush_via_data_or_seek(
    device: str,
    disc: DiscInfo | None,
    avoid_start: int | None,
    avoid_end: int | None,
) -> bool:
    """Evict cache using data-track sectors or SCSI SEEK when audio can't."""
    from ready2rip.disc.mmc import MmcDevice

    try:
        mmc = MmcDevice(device, nonblock=False)
    except OSError as exc:
        log.debug('MMC flush open failed: %s', exc)
        return False
    try:
        # CD-Extra / mixed-mode: data track often sits outside the audio image.
        for dt in getattr(disc, 'data_tracks', None) or []:
            start = int(dt.start_sector)
            end = start + max(1, int(dt.length_sectors))
            if _ranges_overlap(start, end, avoid_start, avoid_end):
                continue
            lba = start + min(16, max(0, int(dt.length_sectors) // 2))
            if mmc.read_raw_sectors(lba, 1) is not None:
                log.debug('Cache flush via data track %s LBA %s', dt.number, lba)
                return True

        # Pure audio full-disc image: seek the head far from the avoid mid.
        target = 0
        if avoid_start is not None and avoid_end is not None and avoid_end > avoid_start:
            mid = (avoid_start + avoid_end) // 2
            # Prefer opposite end of the ripped span.
            target = avoid_start if mid > avoid_start + 75 else max(0, avoid_end - 1)
            if target >= avoid_start and target < avoid_end:
                # Still inside — jump toward disc start or just past avoid_end.
                target = 0 if avoid_start > 0 else max(0, avoid_end)
        if mmc.seek_lba(target):
            time.sleep(0.05)
            # Best-effort one-sector read at the seek point (may fail at lead-out).
            mmc.read_raw_sectors(target, 1)
            log.debug('Cache flush via SCSI SEEK to LBA %s', target)
            return True
    except Exception as exc:
        log.debug('MMC cache flush failed: %s', exc)
        return False
    finally:
        mmc.close()
    return False


def _flush_span(
    disc: DiscInfo | None,
    *,
    avoid_start: int | None = None,
    avoid_end: int | None = None,
) -> tuple[str | None, bool]:
    """Return ``(span, clean)`` far from the just-read region.

    *clean* is True when the span does not overlap the avoid window.
    """
    if disc is None or not disc.tracks:
        span = f'[.0]-[.{_PROBE_SECTORS}]'
        clean = not _ranges_overlap(0, _PROBE_SECTORS, avoid_start, avoid_end)
        return span, clean

    candidates: list[tuple[int, int]] = []
    # Prefer end of last track, then start of first audio (after INDEX 01).
    last = disc.tracks[-1]
    last_end = last.start_sector + last.length_sectors
    last_start = max(last.start_sector, last_end - _PROBE_SECTORS)
    if last_start < last_end:
        candidates.append((last_start, last_end))

    first = disc.tracks[0]
    first_start = max(0, first.start_sector)
    first_end = first_start + min(_PROBE_SECTORS, max(1, first.length_sectors))
    if first_end > first_start:
        candidates.append((first_start, first_end))

    # Mid-disc fallback when both ends are inside the avoid window (Copy Image).
    if len(disc.tracks) >= 3:
        mid = disc.tracks[len(disc.tracks) // 2]
        mid_start = mid.start_sector
        mid_end = mid_start + min(_PROBE_SECTORS, max(1, mid.length_sectors))
        if mid_end > mid_start:
            candidates.append((mid_start, mid_end))

    # Audio before the avoid window (e.g. track-1 pause when image starts at INDEX 01).
    if avoid_start is not None and avoid_start > 1:
        pre_end = min(_PROBE_SECTORS, avoid_start)
        if pre_end > 0:
            candidates.append((0, pre_end))

    for start, end in candidates:
        if not _ranges_overlap(start, end, avoid_start, avoid_end):
            return f'[.{start}]-[.{end}]', True

    # Nothing fully clear of the avoid window — pick the candidate whose
    # midpoint is farthest from the avoid midpoint (still better than none).
    if avoid_start is not None and avoid_end is not None and avoid_end > avoid_start:
        avoid_mid = (avoid_start + avoid_end) / 2.0
        best = max(
            candidates,
            key=lambda se: abs(((se[0] + se[1]) / 2.0) - avoid_mid),
            default=None,
        )
        if best is not None:
            return f'[.{best[0]}]-[.{best[1]}]', False

    if candidates:
        start, end = candidates[0]
        return f'[.{start}]-[.{end}]', False
    return f'[.0]-[.{_PROBE_SECTORS}]', False


def _ranges_overlap(
    start: int,
    end: int,
    avoid_start: int | None,
    avoid_end: int | None,
) -> bool:
    if avoid_start is None or avoid_end is None:
        return False
    if avoid_end <= avoid_start or end <= start:
        return False
    return start < avoid_end and end > avoid_start


def _extract_span(
    device: str,
    span: str,
    wav_path: Path,
    *,
    timeout: int,
    quiet: bool = True,
    burst: bool = False,
) -> None:
    from ready2rip.util import find_cdparanoia

    binary = find_cdparanoia()
    if not binary:
        raise RuntimeError('cdparanoia not found')
    cmd = [
        binary,
        '-w',
        '-d',
        device,
    ]
    if burst:
        cmd.append('-Z')  # raw burst — essential for reliable cache timing
    else:
        # Single token — "-z" "20" is misread as track 20 by cdparanoia.
        cmd.append('--never-skip=20')
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
