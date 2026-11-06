# SPDX-License-Identifier: GPL-3.0-or-later
"""Run cdparanoia with streamed ``-e`` progress (no GTK / Gio imports)."""

from __future__ import annotations

import logging
import os
import select
import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from ready2rip.rip.paranoia_stats import (
    ParanoiaStats,
    parse_paranoia_stderr,
    progress_frame_from_line,
)

log = logging.getLogger(__name__)

# Map WAV/frame completion into this span; remaining headroom (→0.99) is for
# secure-mode re-verify when the file is already fully written.
_WRITE_PROGRESS_SPAN = 0.88


def run_paranoia_streaming(
    cmd: list[str],
    *,
    what: str,
    timeout: int | None = None,
    allow_nonzero: bool = False,
    on_extract_progress: Callable[[float], None] | None = None,
    expected_frames: int = 0,
    wav_path: Path | None = None,
    expected_bytes: int | None = None,
    is_cancelled: Callable[[], bool] | None = None,
    register_proc: Callable[[subprocess.Popen], None] | None = None,
    unregister_proc: Callable[[subprocess.Popen], None] | None = None,
) -> ParanoiaStats:
    """Run *cmd*, stream ``-e`` / WAV progress, return :class:`ParanoiaStats`.

    Progress prefers growing WAV size (monotonic) and also tracks ``-e``
    frame lines. ``stdbuf`` line-buffers child stderr when available so
    updates are not stuck behind a full pipe buffer.

    Stderr is read in non-blocking chunks (never ``readline``) so a partial
    line cannot freeze the loop — WAV polling and activity creep keep running.
    A small floor is emitted at process start so seek/startup stalls still
    surface in the UI; creep continues from zero if frames have not arrived.

    Measured write progress is scaled into ``0 … _WRITE_PROGRESS_SPAN`` so
    activity creep can still advance during paranoia's re-verify pass after
    the WAV has stopped growing.
    """
    run_cmd = list(cmd)
    stdbuf = shutil.which('stdbuf')
    if stdbuf is not None and run_cmd and Path(run_cmd[0]).name != 'stdbuf':
        run_cmd = [stdbuf, '-oL', '-eL', *run_cmd]
    log.info('Running %s: %s', what, ' '.join(run_cmd))
    proc: subprocess.Popen | None = None
    stderr_chunks: list[str] = []
    origin_frame: int | None = None
    last_frac = -1.0
    last_emit = 0.0
    # Measured high-water from WAV / forward frames (already scaled).
    measured_frac = 0.0
    last_measure_at = 0.0
    started_at = time.monotonic()
    pcm_expected = (
        max(0, int(expected_bytes) - 44)
        if expected_bytes is not None and expected_bytes > 44
        else 0
    )
    cancelled = is_cancelled or (lambda: False)
    # Incomplete trailing stderr without a newline yet.
    pending = ''

    def emit(frac: float, *, measured: bool = True) -> None:
        nonlocal last_frac, last_emit, measured_frac, last_measure_at
        if on_extract_progress is None:
            return
        frac = max(0.0, min(0.99, frac))
        now = time.monotonic()
        if measured:
            if frac + 1e-9 < measured_frac:
                return
            if frac > measured_frac + 1e-9:
                measured_frac = frac
                last_measure_at = now
            # Equal high-water: do not refresh last_measure_at (allows creep).
        if frac + 1e-9 < last_frac:
            return
        if frac < last_frac + 0.004 and (now - last_emit) < 0.12:
            return
        last_frac = frac
        last_emit = now
        try:
            on_extract_progress(frac)
        except Exception:
            log.debug('Extract progress callback failed', exc_info=True)

    def emit_measured(raw: float) -> None:
        """Scale raw 0..1 write completion into the write span."""
        raw = max(0.0, min(1.0, raw))
        emit(_WRITE_PROGRESS_SPAN * raw, measured=True)

    def emit_activity_creep() -> None:
        """Nudge progress when paranoia is busy but WAV/frames are quiet."""
        if on_extract_progress is None:
            return
        now = time.monotonic()
        # Seek / startup before any frames: seed a floor so status stays live.
        if last_frac < 0.0:
            if (now - started_at) < 0.35:
                return
            emit(0.01, measured=False)
            return
        if last_measure_at <= 0.0 or (now - last_measure_at) < 0.35:
            return
        if (now - last_emit) < 0.15:
            return
        creep = min(0.99, last_frac + 0.01)
        if creep <= last_frac + 1e-9:
            return
        emit(creep, measured=False)

    def consider_line(line: str) -> None:
        nonlocal origin_frame
        frame = progress_frame_from_line(line)
        if frame is None:
            return
        emit_activity_creep()
        if expected_frames <= 0 or frame < 0:
            return
        if origin_frame is None:
            origin_frame = frame
        rel = frame - origin_frame
        if rel < 0:
            # Re-read / verify earlier sectors — keep creep alive.
            return
        emit_measured(rel / float(expected_frames))

    def consider_wav_size() -> None:
        if wav_path is None or pcm_expected <= 0:
            return
        try:
            if not wav_path.is_file():
                return
            size = wav_path.stat().st_size
        except OSError:
            return
        if size <= 44:
            return
        emit_measured((size - 44) / float(pcm_expected))

    def ingest_stderr(data: bytes) -> None:
        nonlocal pending
        if not data:
            return
        text = data.decode('utf-8', errors='replace')
        stderr_chunks.append(text)
        pending += text
        while True:
            nl = pending.find('\n')
            if nl < 0:
                break
            line, pending = pending[: nl + 1], pending[nl + 1 :]
            consider_line(line)

    try:
        proc = subprocess.Popen(
            run_cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            # Binary pipe + non-blocking reads: never block on a partial line.
            text=False,
            bufsize=0,
        )
        if register_proc is not None:
            register_proc(proc)
        assert proc.stderr is not None
        fd = proc.stderr.fileno()
        os.set_blocking(fd, False)
        # Immediate floor so the UI leaves "Starting…" before the first frame.
        emit(0.01, measured=True)
        deadline = time.monotonic() + timeout if timeout is not None else None
        while True:
            if cancelled():
                try:
                    proc.terminate()
                except OSError:
                    pass
                break
            if deadline is not None and time.monotonic() > deadline:
                proc.kill()
                try:
                    proc.communicate(timeout=5)
                except Exception:
                    pass
                raise RuntimeError(f'{what} timed out after {timeout}s') from None
            consider_wav_size()
            emit_activity_creep()
            try:
                ready, _, _ = select.select([fd], [], [], 0.15)
            except (ValueError, OSError):
                ready = []
            if ready:
                try:
                    chunk = os.read(fd, 8192)
                except BlockingIOError:
                    chunk = b''
                if chunk:
                    ingest_stderr(chunk)
                elif proc.poll() is not None:
                    # EOF
                    break
            elif proc.poll() is not None:
                # Drain any remaining bytes.
                while True:
                    try:
                        chunk = os.read(fd, 8192)
                    except BlockingIOError:
                        break
                    if not chunk:
                        break
                    ingest_stderr(chunk)
                break
            else:
                # No stderr yet — still allow creep while the drive seeks.
                emit_activity_creep()
        # Flush a trailing partial line (rare).
        if pending.strip():
            consider_line(pending)
            pending = ''
        code = proc.wait() if proc.returncode is None else proc.returncode
        consider_wav_size()
        if on_extract_progress is not None and not cancelled():
            try:
                on_extract_progress(1.0)
            except Exception:
                pass
        stderr = ''.join(stderr_chunks)
        stats = parse_paranoia_stderr(stderr, exit_code=code)
        if cancelled():
            return stats
        if code != 0:
            detail = (stderr or '').strip() or f'exit {code}'
            if stats.raw_excerpt:
                detail = stats.raw_excerpt
            elif len(detail) > 400:
                detail = detail[:400] + '…'
            if allow_nonzero:
                log.warning(
                    '%s exited %s (will validate output); paranoia=%s',
                    what,
                    code,
                    stats.short_status(),
                )
                return stats
            raise RuntimeError(f'{what} failed: {detail}')
        return stats
    except OSError as exc:
        raise RuntimeError(f'Failed to run {what}: {exc}') from exc
    finally:
        if proc is not None and unregister_proc is not None:
            unregister_proc(proc)
