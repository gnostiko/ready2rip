# SPDX-License-Identifier: GPL-3.0-or-later
"""One-time CD drive AccurateRip sample-offset calibration."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ready2rip.accuraterip import (
    POPULAR_OFFSETS,
    AccurateRipDatabase,
    compute_checksums_samples,
    disc_ids_from_info,
    fetch_database,
    load_cdda_wav_samples,
)
from ready2rip.disc.probe import DiscInfo, TrackInfo, probe_disc
from ready2rip.util import find_cdparanoia

log = logging.getLogger(__name__)

ProgressCb = Callable[[str, float], None]  # message, fraction 0..1
CancelCb = Callable[[], bool]

# Hard cap for the entire calibration session (extract + offset scan).
CALIBRATION_BUDGET_SEC = 180.0
# Leave a little room for saving results after the deadline check.
_EXTRACT_TIMEOUT_SEC = 90
# Try a few tracks when the first extract or match fails.
_MAX_CALIBRATION_TRACKS = 3
# AccurateRip skips 5 sectors at the start of track 1 / end of the last track.
# Offset detection must use those tracks so a non-zero offset's silence padding
# falls inside the skipped window (middle tracks can never match +6, etc.).
_AR_SKIP_FRAMES = 5 * 588
_MIN_CALIBRATION_SAMPLES = _AR_SKIP_FRAMES * 2 + 588


@dataclass
class CalibrationResult:
    success: bool
    offset: int | None = None
    message: str = ''
    track_number: int | None = None
    confidence: int = 0
    disc_id: str = ''


def needs_drive_setup(store) -> bool:
    """True if the user has not finished setup for the current drive."""
    settings = store.get()
    if not settings.drive_offset_configured:
        return True
    # Re-prompt if the calibrated device path changed.
    if settings.drive_offset_device and settings.device:
        if os.path.realpath(settings.drive_offset_device) != os.path.realpath(
            settings.device
        ):
            return True
    return False


def calibrate_drive_offset(
    device: str = '/dev/sr0',
    *,
    on_progress: ProgressCb | None = None,
    should_cancel: CancelCb | None = None,
) -> CalibrationResult:
    """Find AccurateRip sample offset for *device*.

    Strategy (fast path first):
      1. Burst-extract a mid-length track (retry a few tracks on failure).
      2. Try the most popular AccurateRip offsets first.
      3. Native full-range scan, then a Python fallback if needed.

    The whole procedure is capped at :data:`CALIBRATION_BUDGET_SEC` (3 minutes).
    """
    deadline = time.monotonic() + CALIBRATION_BUDGET_SEC

    def progress(msg: str, frac: float) -> None:
        if on_progress is not None:
            on_progress(msg, max(0.0, min(1.0, frac)))

    def cancelled() -> bool:
        return should_cancel is not None and should_cancel()

    def remaining() -> float:
        return deadline - time.monotonic()

    def timed_out() -> bool:
        return remaining() <= 0.0

    if cancelled():
        return CalibrationResult(success=False, message='Detection cancelled.')

    progress('Reading disc table of contents', 0.02)
    info = probe_disc(device)
    if cancelled():
        return CalibrationResult(success=False, message='Detection cancelled.')
    if info is None or not info.tracks:
        return CalibrationResult(
            success=False,
            message=f'No audio CD found on {device}. Insert a commercial CD and try again.',
        )

    if timed_out():
        return CalibrationResult(
            success=False,
            message=(
                f'Sample offset detection timed out after {int(CALIBRATION_BUDGET_SEC)}s. '
                'Try again or enter offset manually.'
            ),
        )

    ids = disc_ids_from_info(info)
    if ids is None:
        return CalibrationResult(
            success=False,
            message='Could not compute AccurateRip disc IDs for this disc.',
        )

    progress(f'Looking up AccurateRip ({ids.disc_id_string})', 0.10)
    db = fetch_database(ids)
    if cancelled():
        return CalibrationResult(
            success=False,
            message='Detection cancelled.',
            disc_id=ids.disc_id_string,
        )
    if db is None:
        return CalibrationResult(
            success=False,
            message=(
                'This disc is not in the AccurateRip database. '
                'Insert a common commercial CD (pressed, not a home burn) and try again.'
            ),
            disc_id=ids.disc_id_string,
        )

    cdparanoia = find_cdparanoia()
    if cdparanoia is None:
        return CalibrationResult(success=False, message='cdparanoia not found')

    try_tracks = _calibration_tracks(info)
    if not try_tracks:
        return CalibrationResult(
            success=False,
            message=(
                'No track is long enough for AccurateRip offset detection. '
                'Try another commercial CD.'
            ),
            disc_id=ids.disc_id_string,
        )

    last_track_num = None
    extract_failures = 0
    with tempfile.TemporaryDirectory(prefix='ready2rip-cal-') as tmp:
        tmp_path = Path(tmp)
        for index, track in enumerate(try_tracks):
            if cancelled():
                return CalibrationResult(
                    success=False,
                    message='Detection cancelled.',
                    disc_id=ids.disc_id_string,
                    track_number=last_track_num,
                )
            if timed_out():
                break
            last_track_num = track.number
            targets = _targets_for_track(db, track.number)
            if not targets:
                progress(
                    f'Track {track.number} has no AccurateRip entries',
                    0.18 + 0.02 * index,
                )
                continue

            extract_timeout = min(
                _EXTRACT_TIMEOUT_SEC, max(15, int(remaining() - 15))
            )
            progress(
                f'Reading track {track.number} ({track.duration_label})',
                0.20 + 0.05 * index,
            )
            wav_path = tmp_path / f'cal{track.number}.wav'
            try:
                # Burst mode: much faster than full paranoia for offset finding.
                subprocess.run(
                    [
                        cdparanoia,
                        '-q',
                        '-Z',
                        '-w',
                        '-d',
                        device,
                        str(track.number),
                        str(wav_path),
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=extract_timeout,
                )
            except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                extract_failures += 1
                log.warning('Calibration extract track %s failed: %s', track.number, exc)
                progress(
                    f'Could not read track {track.number}; trying another',
                    0.28 + 0.05 * index,
                )
                continue

            if cancelled():
                return CalibrationResult(
                    success=False,
                    message='Detection cancelled.',
                    disc_id=ids.disc_id_string,
                    track_number=track.number,
                )

            if not wav_path.is_file() or wav_path.stat().st_size < 1000:
                extract_failures += 1
                progress(
                    f'Track {track.number} extract was empty; trying another',
                    0.30 + 0.05 * index,
                )
                continue

            if timed_out():
                break

            progress('Loading audio samples', 0.40)
            try:
                samples = load_cdda_wav_samples(wav_path)
            except ValueError as exc:
                log.warning('WAV load failed: %s', exc)
                continue

            progress(f'Checking popular offsets (track {track.number})', 0.45)
            result = _scan_offsets(
                samples,
                track.number,
                info.track_count,
                targets,
                db,
                deadline=deadline,
                on_progress=on_progress,
                should_cancel=should_cancel,
            )
            if cancelled():
                return CalibrationResult(
                    success=False,
                    message='Detection cancelled.',
                    disc_id=ids.disc_id_string,
                    track_number=track.number,
                )
            if result is not None:
                off, conf, _v1, _v2 = result
                offset_msg = (
                    f'Detected sample offset {off:+d} '
                    f'(track {track.number}, confidence {conf})'
                )
                progress('Match found', 1.0)
                return CalibrationResult(
                    success=True,
                    offset=off,
                    confidence=conf,
                    track_number=track.number,
                    disc_id=ids.disc_id_string,
                    message=offset_msg,
                )
            progress(
                f'No match on track {track.number}; trying another',
                0.82,
            )

    if cancelled():
        return CalibrationResult(
            success=False,
            message='Detection cancelled.',
            disc_id=ids.disc_id_string,
            track_number=last_track_num,
        )

    if timed_out():
        return CalibrationResult(
            success=False,
            message=(
                f'Detection stopped after {int(CALIBRATION_BUDGET_SEC)}s without a match. '
                'Try another commercial CD, or enter an offset manually.'
            ),
            disc_id=ids.disc_id_string,
            track_number=last_track_num,
        )

    if extract_failures and last_track_num is None:
        return CalibrationResult(
            success=False,
            message=(
                'Could not read audio from the drive. '
                'Check the disc, close other CD apps, and try again.'
            ),
            disc_id=ids.disc_id_string,
        )

    return CalibrationResult(
        success=False,
        message=(
            'No AccurateRip match for this disc and drive. '
            'Try another commercial CD, or enter the offset manually.'
        ),
        disc_id=ids.disc_id_string,
        track_number=last_track_num,
    )


def _calibration_tracks(info: DiscInfo) -> list[TrackInfo]:
    """Tracks that can detect a non-zero read offset via AccurateRip.

    AccurateRip nulls 5 sectors at the start of track 1 and the end of the
    last track. Applying a sample offset to an uncorrected rip pads the
    opposite edge with silence; that only stays invisible inside those skip
    windows. Middle tracks have no skip, so offsets like +6 never match.

    Order: last track (positive offsets) → first track (negative) → others.
    """
    if not info.tracks:
        return []

    by_num = {t.number: t for t in info.tracks}
    long_enough = [
        t
        for t in info.tracks
        if t.length_sectors * 588 >= _MIN_CALIBRATION_SAMPLES
    ]
    if not long_enough:
        long_enough = list(info.tracks)

    preferred: list[TrackInfo] = []
    last = by_num.get(info.track_count)
    first = by_num.get(1)
    if last is not None and last in long_enough:
        preferred.append(last)
    if first is not None and first in long_enough and first not in preferred:
        preferred.append(first)

    # Longer remaining tracks next — still unlikely to match non-zero offsets,
    # but useful if first/last lack AccurateRip entries.
    rest = sorted(
        (t for t in long_enough if t not in preferred),
        key=lambda t: t.length_sectors,
        reverse=True,
    )
    preferred.extend(rest)
    return preferred[:_MAX_CALIBRATION_TRACKS]


def _targets_for_track(db: AccurateRipDatabase, track_number: int) -> set[int]:
    entries = db.by_track.get(track_number) or []
    return {checksum for checksum, conf in entries if conf > 0}


def _scan_offsets(
    samples,
    track_number: int,
    total_tracks: int,
    targets: set[int],
    db: AccurateRipDatabase,
    *,
    deadline: float,
    on_progress: ProgressCb | None = None,
    should_cancel: CancelCb | None = None,
) -> tuple[int, int, int, int] | None:
    """Return (offset, confidence, v1, v2) or None.

    Order: popular offsets → native full-range → Python popular retry.
    Respects *deadline* (monotonic seconds).
    """
    def remaining() -> float:
        return deadline - time.monotonic()

    def cancelled() -> bool:
        return should_cancel is not None and should_cancel()

    # —— 1) Popular offsets first (Python, but few candidates) ——
    if on_progress is not None:
        on_progress('Checking popular offsets', 0.48)
    hit = _try_offset_list(
        samples,
        track_number,
        total_tracks,
        targets,
        db,
        POPULAR_OFFSETS,
        deadline=deadline,
        on_progress=on_progress,
        should_cancel=should_cancel,
        progress_lo=0.48,
        progress_hi=0.70,
    )
    if hit is not None or cancelled() or remaining() < 5:
        return hit

    # —— 2) Fast native helper over ±2000 ——
    if on_progress is not None:
        on_progress('Scanning full offset range', 0.72)
    native = _scan_with_native(
        samples,
        track_number,
        total_tracks,
        targets,
        timeout=min(45, max(5, int(remaining() - 3))),
    )
    if cancelled():
        return None
    if native is not None and native[0] is not None:
        off, v1, v2 = native[0], native[1], native[2]
        match = db.best_match(track_number, v1, v2)
        if match.confidence.name == 'MATCH':
            return off, match.confidence_count, v1, v2

    # —— 3) Native miss/unavailable: one more Python pass on popular set ——
    # Covers helper build failures and rare checksum edge cases.
    if remaining() < 4 or cancelled():
        return None
    if on_progress is not None:
        on_progress('Retrying popular offsets', 0.88)
    return _try_offset_list(
        samples,
        track_number,
        total_tracks,
        targets,
        db,
        POPULAR_OFFSETS,
        deadline=deadline,
        on_progress=on_progress,
        should_cancel=should_cancel,
        progress_lo=0.88,
        progress_hi=0.97,
    )


def _try_offset_list(
    samples,
    track_number: int,
    total_tracks: int,
    targets: set[int],
    db: AccurateRipDatabase,
    offsets: tuple[int, ...] | list[int],
    *,
    deadline: float,
    on_progress: ProgressCb | None,
    should_cancel: CancelCb | None = None,
    progress_lo: float,
    progress_hi: float,
) -> tuple[int, int, int, int] | None:
    total = max(1, len(offsets))
    for i, off in enumerate(offsets):
        if time.monotonic() >= deadline:
            return None
        if should_cancel is not None and should_cancel():
            return None
        if on_progress is not None and (i % 3 == 0 or i + 1 == total):
            frac = progress_lo + (progress_hi - progress_lo) * (i / total)
            on_progress(f'Trying offset {off:+d} ({i + 1}/{total})', frac)
        v1, v2 = compute_checksums_samples(
            samples, track_number, total_tracks, sample_offset=off
        )
        if v1 not in targets and v2 not in targets:
            continue
        match = db.best_match(track_number, v1, v2)
        if match.confidence.name != 'MATCH':
            continue
        # Fine tune ±5 around the hit.
        best_off, best_conf, best_v1, best_v2 = (
            off,
            match.confidence_count,
            v1,
            v2,
        )
        for fine in range(off - 5, off + 6):
            if fine == off or time.monotonic() >= deadline:
                continue
            fv1, fv2 = compute_checksums_samples(
                samples, track_number, total_tracks, sample_offset=fine
            )
            fm = db.best_match(track_number, fv1, fv2)
            if (
                fm.confidence.name == 'MATCH'
                and fm.confidence_count > best_conf
            ):
                best_off, best_conf, best_v1, best_v2 = (
                    fine,
                    fm.confidence_count,
                    fv1,
                    fv2,
                )
        return best_off, best_conf, best_v1, best_v2
    return None


def _scan_with_native(
    samples,
    track_number: int,
    total_tracks: int,
    targets: set[int],
    *,
    timeout: int = 45,
) -> tuple[int, int, int] | tuple[None, None, None] | None:
    """Use compiled C helper when available.

    Returns:
      (offset, v1, v2) on match
      (None, None, None) if helper ran but found no match
      None if helper unavailable / failed to run (caller may Python-fallback)
    """
    helper = _ensure_native_helper()
    if helper is None:
        return None

    with tempfile.TemporaryDirectory(prefix='ready2rip-ars-') as tmp:
        raw_path = Path(tmp) / 'samples.u32'
        raw_path.write_bytes(samples.tobytes())
        n = len(samples)
        target_args = [f'{t:08x}' for t in sorted(targets)]
        cmd = [
            str(helper),
            str(raw_path),
            str(n),
            str(track_number),
            str(total_tracks),
            '-2000',
            '2000',
            '1',
            *target_args,
        ]
        try:
            completed = subprocess.run(
                cmd,
                check=False,
                capture_output=True,
                text=True,
                timeout=max(5, timeout),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            log.warning('Native offset scan failed: %s', exc)
            return None

        if completed.returncode not in (0,):
            log.warning(
                'Native offset scan exit %s: %s',
                completed.returncode,
                completed.stderr,
            )
            return None

        line = (completed.stdout or '').strip().splitlines()
        if not line:
            return None
        parts = line[-1].split()
        if parts[0] == 'NONE':
            return (None, None, None)
        if parts[0] != 'MATCH' or len(parts) < 4:
            return None
        off = int(parts[1])
        v1 = int(parts[2], 16)
        v2 = int(parts[3], 16)
        return off, v1, v2


def _ensure_native_helper() -> Path | None:
    """Locate or compile the C offset scanner.

    Prefers a shipped binary (``ar_offset_scan`` on PATH, e.g. AppImage
    ``usr/bin``), then a user-cache build from the bundled C source.
    """
    on_path = shutil.which('ar_offset_scan')
    if on_path:
        return Path(on_path)

    src = Path(__file__).resolve().parent / 'native' / 'ar_offset_scan.c'
    if not src.is_file():
        return None
    cc = shutil.which('cc') or shutil.which('gcc')
    if not cc:
        return None

    cache = Path(os.environ.get('XDG_CACHE_HOME', Path.home() / '.cache')) / 'ready2rip'
    cache.mkdir(parents=True, exist_ok=True)
    binary = cache / 'ar_offset_scan'
    if binary.is_file() and binary.stat().st_mtime >= src.stat().st_mtime:
        return binary

    try:
        subprocess.run(
            [cc, '-O3', '-o', str(binary), str(src)],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        log.warning('Could not build ar_offset_scan: %s', exc)
        return None
    return binary if binary.is_file() else None


def save_calibration(
    store,
    *,
    device: str,
    offset: int,
) -> None:
    """Persist sample offset for *device*.

    Values are written to GSettings (or the in-memory fallback) so they survive
    app restarts.
    """
    store.update(
        drive_sample_offset=int(offset),
        drive_offset_configured=True,
        drive_offset_device=device,
        device=device,
    )

    try:
        from gi.repository import Gio

        Gio.Settings.sync()
    except Exception:
        pass

    log.info('Saved drive calibration for %s: offset=%s', device, offset)
