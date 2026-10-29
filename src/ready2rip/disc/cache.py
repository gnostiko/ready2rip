from __future__ import annotations

import logging
import subprocess
import tempfile
import time
import wave
import zlib
from dataclasses import dataclass
from pathlib import Path

from ready2rip.disc.probe import DiscInfo

log = logging.getLogger(__name__)

# Short segment used for cache probes (~2 s of CDDA gives clearer timing).
_PROBE_SECTORS = 150


@dataclass
class DriveCacheResult:
    """Result of an audio-cache analysis pass."""

    caches: bool | None  # True / False / None (unknown / skipped)
    message: str


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
    from ready2rip.util import find_cdparanoia

    if find_cdparanoia() is None:
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

    first_ms = 0.0
    second_ms = 0.0
    third_ms = 0.0
    crc_a: int | None = None
    crc_b: int | None = None
    crc_c: int | None = None

    with tempfile.TemporaryDirectory(prefix='ready2rip-cache-') as tmp:
        tmp_path = Path(tmp)
        a = tmp_path / 'a.wav'
        b = tmp_path / 'b.wav'
        flush_tmp = tmp_path / 'flush.wav'
        try:
            # Cold flush: read something far away so the first probe read must come from media.
            flush_span, _clean = _flush_span(disc)
            flush_span = flush_span or span
            try:
                _extract_span(device, flush_span, flush_tmp, timeout=timeout, quiet=True, burst=True)
            except Exception:
                pass  # best-effort cold start

            # Give the drive a moment to actually perform the flush seek.
            time.sleep(0.06)

            t0 = time.monotonic()
            _extract_span(device, span, a, timeout=timeout, burst=True)
            first_ms = (time.monotonic() - t0) * 1000.0
            crc_a = _pcm_crc(a)

            # Immediate re-read of the exact same span — should hit audio cache if present.
            t1 = time.monotonic()
            _extract_span(device, span, b, timeout=timeout, burst=True)
            second_ms = (time.monotonic() - t1) * 1000.0
            crc_b = _pcm_crc(b)

            # Optional third read for confirmation when it looks fast.
            if crc_b is not None:
                t2 = time.monotonic()
                try:
                    c = tmp_path / 'c.wav'
                    _extract_span(device, span, c, timeout=timeout, burst=True)
                    third_ms = (time.monotonic() - t2) * 1000.0
                    crc_c = _pcm_crc(c)
                except Exception:
                    third_ms = 0.0
                    crc_c = None
        except Exception as exc:
            log.warning('Drive cache detection failed: %s', exc)
            return DriveCacheResult(
                caches=None,
                message=f'Cache detection failed: {exc}',
            )

    match = crc_a is not None and crc_a == crc_b and crc_a != 0
    if match and crc_c is not None and crc_c != crc_a:
        match = False

    if not match:
        # CRC mismatch means we can't trust the timing comparison.
        # Don't claim "no cache" — leave it unknown so defeat can still be on by default.
        message = (
            f'Cache probe CRCs differ (unstable read); '
            f'times {first_ms:.0f}/{second_ms:.0f} ms'
        )
        caches = None
    else:
        ratio = second_ms / first_ms if first_ms > 1.0 else 1.0
        abs_speedup = first_ms - second_ms

        # Heuristic tuned for both old mechanical drives and faster USB/Blu-ray ones.
        # Requires first read to have taken some real time, and second to be substantially faster.
        if (first_ms > 15.0 and second_ms < 400.0 and
                (ratio <= 0.48 or (second_ms < 150.0 and abs_speedup >= 18.0))):
            caches = True
            message = (
                f'Drive caches audio (re-read {second_ms:.0f} ms vs '
                f'{first_ms:.0f} ms, CRC match)'
            )
        else:
            message = (
                f'No clear audio cache (re-read {second_ms:.0f} ms vs '
                f'{first_ms:.0f} ms, CRC match)'
            )
            caches = False

    log.info(
        'Drive cache detection: %s (first=%.0f ms, second=%.0f ms, third=%.0f ms, CRC match=%s)',
        message,
        first_ms,
        second_ms,
        third_ms,
        match,
    )
    return DriveCacheResult(
        caches=caches,
        message=message,
    )


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


def _pcm_crc(wav_path: Path) -> int | None:
    """CRC32 of WAV PCM frames (RIFF header skipped). Used by cache and Accurate Stream probes."""
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
    except Exception:
        try:
            raw = wav_path.read_bytes()
            pcm = raw[44:] if len(raw) > 44 and raw[:4] == b'RIFF' else raw
            return zlib.crc32(pcm) & 0xFFFFFFFF
        except OSError:
            return None
