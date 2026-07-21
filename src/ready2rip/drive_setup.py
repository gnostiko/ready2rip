# SPDX-License-Identifier: GPL-3.0-or-later
"""One-time CD drive AccurateRip sample-offset calibration."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ready2rip.accuraterip import (
    AccurateRipDatabase,
    POPULAR_OFFSETS,
    compute_checksums_samples,
    disc_ids_from_info,
    fetch_database,
    load_cdda_wav_samples,
)
from ready2rip.disc.cache import detect_drive_cache
from ready2rip.disc.features import (
    FeatureTestResult,
    test_accurate_stream,
    test_c2_pointers,
)
from ready2rip.disc.probe import probe_disc
from ready2rip.util import find_cdparanoia

log = logging.getLogger(__name__)

ProgressCb = Callable[[str, float], None]  # message, fraction 0..1

# Hard cap for the entire calibration session (feature tests + extract + scan).
CALIBRATION_BUDGET_SEC = 180.0
# Leave a little room for saving results after the deadline check.
_EXTRACT_TIMEOUT_SEC = 90
_FEATURE_TIMEOUT_SEC = 25


@dataclass
class CalibrationResult:
    success: bool
    offset: int | None = None
    message: str = ''
    track_number: int | None = None
    confidence: int = 0
    disc_id: str = ''
    # Cache analysis (run during drive setup, persisted with the offset).
    caches_audio: bool | None = None
    cache_message: str = ''
    # Accurate Stream + C2 (EAC-style drive feature tests).
    accurate_stream: bool | None = None
    accurate_stream_message: str = ''
    c2_pointers: bool | None = None
    c2_message: str = ''


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
) -> CalibrationResult:
    """Analyze drive features, then find AccurateRip sample offset.

    Strategy (fast path first):
      1. Quick C2 / Accurate Stream / cache probes (time-boxed).
      2. Burst-extract one mid-length track.
      3. Try the most popular AccurateRip offsets first.
      4. Only then try a wider native/Python scan if time remains.

    The whole procedure is capped at :data:`CALIBRATION_BUDGET_SEC` (3 minutes).
    """
    deadline = time.monotonic() + CALIBRATION_BUDGET_SEC

    def progress(msg: str, frac: float) -> None:
        if on_progress is not None:
            on_progress(msg, max(0.0, min(1.0, frac)))

    def remaining() -> float:
        return deadline - time.monotonic()

    def timed_out() -> bool:
        return remaining() <= 0.0

    progress('Reading disc table of contents…', 0.02)
    info = probe_disc(device)
    if info is None or not info.tracks:
        return CalibrationResult(
            success=False,
            message=f'No audio CD found on {device}. Insert a commercial CD and try again.',
        )

    # Feature analysis — keep these short so offset search gets most of the budget.
    progress('Testing C2 error pointers…', 0.04)
    c2 = test_c2_pointers(device)
    progress(c2.message, 0.06)

    if not timed_out():
        progress('Testing Accurate Stream…', 0.07)
        astream = test_accurate_stream(
            device,
            info,
            trials=1,
            timeout=min(_FEATURE_TIMEOUT_SEC, max(5, int(remaining() - 30))),
        )
        progress(astream.message, 0.11)
    else:
        astream = FeatureTestResult(
            supported=None, message='Accurate Stream skipped (time budget)'
        )

    if not timed_out():
        progress('Analyzing drive audio cache…', 0.12)
        cache = detect_drive_cache(
            device,
            info,
            timeout=min(_FEATURE_TIMEOUT_SEC, max(5, int(remaining() - 20))),
        )
        progress(cache.message, 0.16)
    else:
        from ready2rip.disc.cache import DriveCacheResult

        cache = DriveCacheResult(
            caches=None, message='Cache test skipped (time budget)'
        )

    def with_cache(**kwargs) -> CalibrationResult:
        return CalibrationResult(
            caches_audio=cache.caches,
            cache_message=cache.message,
            accurate_stream=astream.supported,
            accurate_stream_message=astream.message,
            c2_pointers=c2.supported,
            c2_message=c2.message,
            **kwargs,
        )

    if timed_out():
        return with_cache(
            success=False,
            message=(
                f'Calibration timed out after {int(CALIBRATION_BUDGET_SEC)}s '
                'during drive feature tests. Try again or enter offset manually.'
            ),
        )

    ids = disc_ids_from_info(info)
    if ids is None:
        return with_cache(
            success=False,
            message='Could not compute AccurateRip disc IDs for this disc.',
        )

    progress(f'Looking up AccurateRip database ({ids.disc_id_string})…', 0.18)
    db = fetch_database(ids)
    if db is None:
        return with_cache(
            success=False,
            message=(
                'This disc is not in the AccurateRip database. '
                'Insert a common commercial CD (pressed, not a home burn) and try again.'
            ),
            disc_id=ids.disc_id_string,
        )

    cdparanoia = find_cdparanoia()
    if cdparanoia is None:
        return with_cache(success=False, message='cdparanoia not found')

    # One mid-length track is enough for offset detection; full multi-track
    # re-rips made calibration last tens of minutes.
    ordered = sorted(info.tracks, key=lambda t: t.length_sectors)
    middle = [
        t
        for t in ordered
        if t.number not in (1, info.track_count) or info.track_count <= 2
    ]
    try_tracks = (middle + [t for t in ordered if t not in middle])[:1]

    last_track_num = None
    with tempfile.TemporaryDirectory(prefix='ready2rip-cal-') as tmp:
        tmp_path = Path(tmp)
        for track in try_tracks:
            if timed_out():
                break
            last_track_num = track.number
            targets = _targets_for_track(db, track.number)
            if not targets:
                continue

            extract_timeout = min(
                _EXTRACT_TIMEOUT_SEC, max(15, int(remaining() - 15))
            )
            progress(
                f'Extracting track {track.number} ({track.duration_label}) '
                f'for calibration (burst, ≤{extract_timeout}s)…',
                0.22,
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
                log.warning('Calibration extract track %s failed: %s', track.number, exc)
                continue

            if not wav_path.is_file() or wav_path.stat().st_size < 1000:
                continue

            if timed_out():
                break

            progress('Loading audio samples…', 0.40)
            try:
                samples = load_cdda_wav_samples(wav_path)
            except ValueError as exc:
                log.warning('WAV load failed: %s', exc)
                continue

            progress(
                f'Trying popular sample offsets (track {track.number})…',
                0.45,
            )
            result = _scan_offsets(
                samples,
                track.number,
                info.track_count,
                targets,
                db,
                deadline=deadline,
                on_progress=on_progress,
            )
            if result is not None:
                off, conf, v1, v2 = result
                offset_msg = (
                    f'Detected sample offset {off} '
                    f'(AccurateRip match on track {track.number}, confidence {conf})'
                )
                return with_cache(
                    success=True,
                    offset=off,
                    confidence=conf,
                    track_number=track.number,
                    disc_id=ids.disc_id_string,
                    message=offset_msg,
                )

    if timed_out():
        return with_cache(
            success=False,
            message=(
                f'Calibration stopped after {int(CALIBRATION_BUDGET_SEC)}s without a match. '
                'Try another commercial CD, or enter an offset manually '
                '(see accuraterip.com drive offsets).'
            ),
            disc_id=ids.disc_id_string,
            track_number=last_track_num,
        )

    return with_cache(
        success=False,
        message=(
            'Could not find a matching AccurateRip offset for this disc/drive. '
            'You can enter an offset manually from accuraterip.com/driveoffsets.htm, '
            'or try another commercial CD.'
        ),
        disc_id=ids.disc_id_string,
        track_number=last_track_num,
    )


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
) -> tuple[int, int, int, int] | None:
    """Return (offset, confidence, v1, v2) or None.

    Order: popular offsets → native full-range (if available) → remaining common.
    Respects *deadline* (monotonic seconds).
    """
    def remaining() -> float:
        return deadline - time.monotonic()

    # —— 1) Popular offsets first (Python, but few candidates) ——
    if on_progress is not None:
        on_progress('Checking popular drive offsets…', 0.48)
    hit = _try_offset_list(
        samples,
        track_number,
        total_tracks,
        targets,
        db,
        POPULAR_OFFSETS,
        deadline=deadline,
        on_progress=on_progress,
        progress_lo=0.48,
        progress_hi=0.72,
    )
    if hit is not None:
        return hit
    if remaining() < 5:
        return None

    # —— 2) Fast native helper over ±2000 (seconds in C) ——
    if on_progress is not None:
        on_progress('Scanning offsets with fast helper…', 0.74)
    native = _scan_with_native(
        samples,
        track_number,
        total_tracks,
        targets,
        timeout=min(45, max(5, int(remaining() - 3))),
    )
    if native is not None:
        if native[0] is None:
            return None
        off, v1, v2 = native[0], native[1], native[2]
        match = db.best_match(track_number, v1, v2)
        return off, match.confidence_count, v1, v2
    return None


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
    progress_lo: float,
    progress_hi: float,
) -> tuple[int, int, int, int] | None:
    total = max(1, len(offsets))
    for i, off in enumerate(offsets):
        if time.monotonic() >= deadline:
            return None
        if on_progress is not None and (i % 3 == 0 or i + 1 == total):
            frac = progress_lo + (progress_hi - progress_lo) * (i / total)
            on_progress(f'Trying sample offset {off}… ({i + 1}/{total})', frac)
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
        # Prefer popular band first? Full ±2000 step 1 is still only seconds in C.
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
    caches_audio: bool | None = None,
    cache_message: str = '',
    accurate_stream: bool | None = None,
    accurate_stream_message: str = '',
    c2_pointers: bool | None = None,
    c2_message: str = '',
) -> None:
    """Persist sample offset and optional feature analysis for *device*.

    Values are written to GSettings (or the in-memory fallback) so they survive
    app restarts. When *caches_audio* is True, cache defeat is also enabled.
    """
    updates: dict = {
        'drive_sample_offset': int(offset),
        'drive_offset_configured': True,
        'drive_offset_device': device,
        'device': device,
    }
    if caches_audio is not None:
        updates['drive_cache_configured'] = True
        updates['drive_caches_audio'] = bool(caches_audio)
        updates['drive_cache_message'] = cache_message or (
            'Drive caches audio' if caches_audio else 'No clear audio cache'
        )
        if caches_audio:
            updates['defeat_audio_cache'] = True

    if accurate_stream is not None:
        updates['drive_accurate_stream_configured'] = True
        updates['drive_accurate_stream'] = bool(accurate_stream)
        updates['drive_accurate_stream_message'] = accurate_stream_message or (
            'Accurate Stream: yes' if accurate_stream else 'Accurate Stream: no'
        )

    if c2_pointers is not None:
        updates['drive_c2_configured'] = True
        updates['drive_c2_pointers'] = bool(c2_pointers)
        updates['drive_c2_message'] = c2_message or (
            'C2 Error pointers: yes' if c2_pointers else 'C2 Error pointers: no'
        )

    store.update(**updates)

    try:
        from gi.repository import Gio

        Gio.Settings.sync()
    except Exception:  # noqa: BLE001
        pass

    log.info(
        'Saved drive calibration for %s: offset=%s caches=%s astream=%s c2=%s',
        device,
        offset,
        caches_audio,
        accurate_stream,
        c2_pointers,
    )
