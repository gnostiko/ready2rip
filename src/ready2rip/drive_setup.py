# SPDX-License-Identifier: GPL-3.0-or-later
"""One-time CD drive AccurateRip sample-offset calibration."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ready2rip.accuraterip import (
    AccurateRipDatabase,
    COMMON_OFFSETS,
    compute_checksums_samples,
    disc_ids_from_info,
    fetch_database,
    load_cdda_wav_samples,
)
from ready2rip.disc.cache import detect_drive_cache
from ready2rip.disc.features import test_accurate_stream, test_c2_pointers
from ready2rip.disc.probe import probe_disc

log = logging.getLogger(__name__)

ProgressCb = Callable[[str, float], None]  # message, fraction 0..1


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
    """Analyze drive cache, then find AccurateRip sample offset.

    Both results are meant to be persisted via :func:`save_calibration`.
    """

    def progress(msg: str, frac: float) -> None:
        if on_progress is not None:
            on_progress(msg, max(0.0, min(1.0, frac)))

    progress('Reading disc table of contents…', 0.02)
    info = probe_disc(device)
    if info is None or not info.tracks:
        return CalibrationResult(
            success=False,
            message=f'No audio CD found on {device}. Insert a commercial CD and try again.',
        )

    # Feature analysis first (whipper/EAC-style drive analyze) — needs a disc.
    progress('Testing C2 error pointers…', 0.04)
    c2 = test_c2_pointers(device)
    progress(c2.message, 0.06)

    progress('Testing Accurate Stream…', 0.07)
    astream = test_accurate_stream(device, info)
    progress(astream.message, 0.11)

    progress('Analyzing drive audio cache…', 0.12)
    cache = detect_drive_cache(device, info)
    progress(cache.message, 0.16)

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

    ids = disc_ids_from_info(info)
    if ids is None:
        return with_cache(
            success=False,
            message='Could not compute AccurateRip disc IDs for this disc.',
        )

    progress(f'Looking up AccurateRip database ({ids.disc_id_string})…', 0.15)
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

    from ready2rip.util import find_cdparanoia

    cdparanoia = find_cdparanoia()
    if cdparanoia is None:
        return with_cache(success=False, message='cdparanoia not found')

    # Try shortest middle track first, then other short tracks if needed.
    ordered = sorted(info.tracks, key=lambda t: t.length_sectors)
    middle = [
        t
        for t in ordered
        if t.number not in (1, info.track_count) or info.track_count <= 2
    ]
    try_tracks = (middle + [t for t in ordered if t not in middle])[:3]

    last_track_num = None
    with tempfile.TemporaryDirectory(prefix='ready2rip-cal-') as tmp:
        tmp_path = Path(tmp)
        for attempt, track in enumerate(try_tracks):
            last_track_num = track.number
            targets = _targets_for_track(db, track.number)
            if not targets:
                continue

            progress(
                f'Extracting track {track.number} ({track.duration_label}) '
                f'for calibration…',
                0.18 + 0.05 * attempt,
            )
            wav_path = tmp_path / f'cal{track.number}.wav'
            try:
                subprocess.run(
                    [
                        cdparanoia,
                        '-q',
                        '-w',
                        '-d',
                        device,
                        str(track.number),
                        str(wav_path),
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=600,
                )
            except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                log.warning('Calibration extract track %s failed: %s', track.number, exc)
                continue

            if not wav_path.is_file() or wav_path.stat().st_size < 1000:
                continue

            progress('Loading audio samples…', 0.40)
            try:
                samples = load_cdda_wav_samples(wav_path)
            except ValueError as exc:
                log.warning('WAV load failed: %s', exc)
                continue

            progress(
                f'Scanning sample offsets (track {track.number})…',
                0.45,
            )
            result = _scan_offsets(
                samples,
                track.number,
                info.track_count,
                targets,
                db,
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
    on_progress: ProgressCb | None = None,
) -> tuple[int, int, int, int] | None:
    """Return (offset, confidence, v1, v2) or None."""
    # Prefer native C scanner (full ±2000 in seconds).
    if on_progress is not None:
        on_progress('Scanning offsets with fast helper…', 0.45)
    native = _scan_with_native(samples, track_number, total_tracks, targets)
    if native is not None:
        if native[0] is None:
            # Helper ran; no match in range — skip slow Python rescan.
            return None
        off, v1, v2 = native[0], native[1], native[2]
        match = db.best_match(track_number, v1, v2)
        return off, match.confidence_count, v1, v2

    # Python fallback when cc/gcc is unavailable.
    offsets: list[int] = []
    seen: set[int] = set()
    for off in COMMON_OFFSETS:
        if off not in seen:
            seen.add(off)
            offsets.append(off)
    for off in range(-1500, 1501, 6):
        if off not in seen:
            seen.add(off)
            offsets.append(off)

    total = len(offsets)
    for i, off in enumerate(offsets):
        if on_progress is not None and i % 5 == 0:
            on_progress(
                f'Trying sample offset {off}… ({i + 1}/{total})',
                0.4 + 0.55 * (i / max(1, total)),
            )
        v1, v2 = compute_checksums_samples(
            samples, track_number, total_tracks, sample_offset=off
        )
        if v1 in targets or v2 in targets:
            match = db.best_match(track_number, v1, v2)
            if match.confidence.name == 'MATCH':
                for fine in range(off - 5, off + 6):
                    if fine == off:
                        continue
                    fv1, fv2 = compute_checksums_samples(
                        samples, track_number, total_tracks, sample_offset=fine
                    )
                    fm = db.best_match(track_number, fv1, fv2)
                    if (
                        fm.confidence.name == 'MATCH'
                        and fm.confidence_count > match.confidence_count
                    ):
                        return fine, fm.confidence_count, fv1, fv2
                return off, match.confidence_count, v1, v2
    return None


def _scan_with_native(
    samples,
    track_number: int,
    total_tracks: int,
    targets: set[int],
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
                timeout=300,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            log.warning('Native offset scan failed: %s', exc)
            return None

        if completed.returncode not in (0,):
            log.warning('Native offset scan exit %s: %s', completed.returncode, completed.stderr)
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
    """Compile the C offset scanner into the user cache if needed."""
    src = Path(__file__).resolve().parent / 'native' / 'ar_offset_scan.c'
    if not src.is_file():
        return None
    cc = shutil.which('cc') or shutil.which('gcc')
    if not cc:
        return None

    cache = Path(os.environ.get('XDG_CACHE_HOME', Path.home() / '.cache')) / 'ready2rip'
    cache.mkdir(parents=True, exist_ok=True)
    binary = cache / 'ar_offset_scan'
    # Rebuild if source is newer or missing.
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

    # Force backend flush so offset/features survive process exit immediately.
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
