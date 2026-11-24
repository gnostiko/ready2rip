# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared secure test-and-copy session (CRC dual-pass + optional burst recovery).

Callers supply extract backends (HTOA / per-track / Copy Image). This module
owns retry / flush / CRC-match / burst-recovery policy only.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ready2rip.rip.progress_pulse import ProgressPulse
from ready2rip.rip.riplog import analyze_wav_for_log, wav_pcm_crc

log = logging.getLogger(__name__)

ExtractFn = Callable[[Path], str]
FlushFn = Callable[[], None]
TickFn = Callable[[], None]
UnlinkFn = Callable[..., None]


@dataclass
class SecureTestCopyResult:
    """Outcome of a secure dual-pass session (and optional burst recovery)."""

    test_crc: str = ''
    copy_crc: str = ''
    # Last secure dual-pass pair (kept after burst so callers can log the mismatch).
    secure_test_crc: str = ''
    secure_copy_crc: str = ''
    matched: bool = False
    mode: str = 'secure'
    test_mode: str = ''
    copy_mode: str = ''
    burst_recovered: bool = False
    attempt_log: list[str] = field(default_factory=list)
    peak: float | None = None
    error: str | None = None


def run_secure_test_and_copy(
    *,
    test_wav: Path,
    copy_wav: Path,
    max_retries: int,
    burst_fallback: bool,
    extract_test: ExtractFn,
    extract_copy: ExtractFn,
    extract_burst: ExtractFn | None = None,
    flush: FlushFn | None = None,
    is_cancelled: Callable[[], bool] | None = None,
    unlink: UnlinkFn | None = None,
    reuse_initial_test: bool = False,
    after_test: Callable[[Path], None] | None = None,
    after_copy: Callable[[Path], None] | None = None,
    after_burst: Callable[[Path], None] | None = None,
    test_crc_tick: TickFn | None = None,
    copy_crc_tick: TickFn | None = None,
    on_mismatch: Callable[[str, str, int], None] | None = None,
    on_before_burst: TickFn | None = None,
    unlink_test_after_crc: bool = True,
    compute_peak: bool = False,
    label: str = 'track',
) -> SecureTestCopyResult:
    """Run secure test & copy with retries, then optional burst recovery.

    *reuse_initial_test*: when True, attempt 0 uses an already-filled *test_wav*
    (HTOA / Copy Image first pass) and only re-extracts test on retries.

    *extract_* callables return a short mode label (``secure`` / ``burst`` / …).
    """
    attempts = 1 + max(0, int(max_retries))
    result = SecureTestCopyResult()
    cancelled = is_cancelled or (lambda: False)
    last_test = ''
    last_copy = ''

    def _unlink(*paths: Path) -> None:
        if unlink is not None:
            unlink(*paths)
            return
        for path in paths:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    def _crc(path: Path, tick: TickFn | None) -> str:
        if tick is None:
            return wav_pcm_crc(path)
        with ProgressPulse(tick):
            return wav_pcm_crc(path)

    def _peak_from(path: Path) -> float | None:
        if not compute_peak:
            return None
        _crc_hex, peak = analyze_wav_for_log(path)
        return peak

    def _burst_recover(*, reason: str) -> SecureTestCopyResult:
        assert extract_burst is not None
        if on_before_burst is not None:
            on_before_burst()
        if flush is not None:
            flush()
        result.copy_mode = extract_burst(copy_wav) or 'burst'
        if after_burst is not None:
            after_burst(copy_wav)
        result.copy_crc = _crc(copy_wav, copy_crc_tick)
        result.peak = _peak_from(copy_wav)
        result.mode = 'burst'
        result.burst_recovered = True
        # Final audio is burst — do not keep secure test CRC as verified.
        result.test_crc = ''
        result.matched = False
        result.attempt_log.append(
            f'burst recovery CRC={result.copy_crc.upper() or "?"}'
        )
        result.error = reason
        return result

    for attempt in range(attempts):
        if cancelled():
            break
        try:
            need_test_extract = not (reuse_initial_test and attempt == 0)
            if need_test_extract:
                result.test_mode = extract_test(test_wav) or 'secure'
                if after_test is not None:
                    after_test(test_wav)
            elif not result.test_mode:
                result.test_mode = 'secure'

            result.test_crc = _crc(test_wav, test_crc_tick)
            last_test = result.test_crc
            result.secure_test_crc = last_test
            if unlink_test_after_crc:
                _unlink(test_wav)

            if flush is not None:
                flush()

            try:
                result.copy_mode = extract_copy(copy_wav) or 'secure'
            except Exception as copy_exc:
                if not (burst_fallback and extract_burst is not None and not cancelled()):
                    raise
                log.warning('%s copy pass failed (%s); burst recovery', label, copy_exc)
                result.attempt_log.append(
                    f'pass {attempt + 1}: copy failed ({copy_exc})'
                )
                return _burst_recover(
                    reason=(
                        f'{label} copy pass failed secure; recovered in burst '
                        f'(skipped test/copy CRC)'
                    )
                )

            if after_copy is not None:
                after_copy(copy_wav)

            result.copy_crc = _crc(copy_wav, copy_crc_tick)
            last_copy = result.copy_crc
            result.secure_copy_crc = last_copy
            result.peak = _peak_from(copy_wav)
            result.mode = result.copy_mode
            result.attempt_log.append(
                f'pass {attempt + 1}: test={result.test_mode}/'
                f'{result.test_crc.upper() or "?"} copy={result.copy_mode}/'
                f'{result.copy_crc.upper() or "?"}'
            )

            if result.test_crc and result.copy_crc and result.test_crc == result.copy_crc:
                result.matched = True
                result.attempt_log.append(
                    f'pass {attempt + 1}: CRC match {result.copy_crc.upper()}'
                )
                return result

            result.matched = False
            result.error = (
                f'{label} test/copy CRC mismatch '
                f'(test={result.test_crc} copy={result.copy_crc})'
            )
            result.attempt_log.append(
                f'pass {attempt + 1}: CRC MISMATCH '
                f'test={result.test_crc.upper()} copy={result.copy_crc.upper()}'
            )
            if on_mismatch is not None:
                on_mismatch(result.test_crc, result.copy_crc, attempt)
            _unlink(test_wav, copy_wav)
        except Exception as exc:
            result.error = str(exc)
            result.matched = False
            result.attempt_log.append(f'pass {attempt + 1}: FAILED — {exc}')
            log.warning('%s attempt %s failed: %s', label, attempt, exc)
            _unlink(test_wav, copy_wav)
            if attempt + 1 >= attempts:
                break

    if result.matched or cancelled():
        return result

    if burst_fallback and extract_burst is not None and not cancelled():
        try:
            return _burst_recover(
                reason=(
                    f'{label} test/copy CRC mismatch '
                    f'(test={last_test} copy={last_copy}); recovered in burst'
                )
            )
        except Exception as exc:
            result.error = str(exc)
            result.attempt_log.append(f'burst recovery FAILED — {exc}')
            log.warning('%s burst recovery failed: %s', label, exc)

    return result
