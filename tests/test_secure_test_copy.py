# SPDX-License-Identifier: GPL-3.0-or-later
"""Secure test-and-copy session helper (no optical hardware)."""

from __future__ import annotations

from pathlib import Path

from ready2rip.rip.test_copy import run_secure_test_and_copy


def _write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def test_match_on_first_pass(tmp_path: Path) -> None:
    test_wav = tmp_path / 't.wav'
    copy_wav = tmp_path / 'c.wav'
    pcm = b'RIFF' + b'\x01\x02' * 200

    def extract_test(dest: Path) -> str:
        _write(dest, pcm)
        return 'secure'

    def extract_copy(dest: Path) -> str:
        _write(dest, pcm)
        return 'secure'

    result = run_secure_test_and_copy(
        test_wav=test_wav,
        copy_wav=copy_wav,
        max_retries=2,
        burst_fallback=False,
        extract_test=extract_test,
        extract_copy=extract_copy,
        label='track 1',
    )
    assert result.matched is True
    assert result.burst_recovered is False
    assert result.test_crc == result.copy_crc
    assert result.test_crc
    assert any('CRC match' in line for line in result.attempt_log)


def test_retry_then_match(tmp_path: Path) -> None:
    test_wav = tmp_path / 't.wav'
    copy_wav = tmp_path / 'c.wav'
    good = b'RIFF' + b'\x11\x22' * 200
    bad = b'RIFF' + b'\x33\x44' * 200
    calls = {'n': 0}

    def extract_test(dest: Path) -> str:
        _write(dest, good)
        return 'secure'

    def extract_copy(dest: Path) -> str:
        calls['n'] += 1
        _write(dest, bad if calls['n'] == 1 else good)
        return 'secure'

    mismatches: list[tuple[str, str, int]] = []
    result = run_secure_test_and_copy(
        test_wav=test_wav,
        copy_wav=copy_wav,
        max_retries=2,
        burst_fallback=False,
        extract_test=extract_test,
        extract_copy=extract_copy,
        on_mismatch=lambda t, c, a: mismatches.append((t, c, a)),
        label='track 2',
    )
    assert result.matched is True
    assert len(mismatches) == 1
    assert mismatches[0][2] == 0
    assert any('CRC MISMATCH' in line for line in result.attempt_log)
    assert any('CRC match' in line for line in result.attempt_log)


def test_exhaust_retries_then_burst(tmp_path: Path) -> None:
    test_wav = tmp_path / 't.wav'
    copy_wav = tmp_path / 'c.wav'
    good = b'RIFF' + b'\xaa\xbb' * 200
    bad = b'RIFF' + b'\xcc\xdd' * 200
    burst_pcm = b'RIFF' + b'\xee\xff' * 200
    modes: list[str] = []

    def extract_test(dest: Path) -> str:
        _write(dest, good)
        return 'secure'

    def extract_copy(dest: Path) -> str:
        _write(dest, bad)
        return 'secure'

    def extract_burst(dest: Path) -> str:
        modes.append('burst')
        _write(dest, burst_pcm)
        return 'burst'

    result = run_secure_test_and_copy(
        test_wav=test_wav,
        copy_wav=copy_wav,
        max_retries=1,
        burst_fallback=True,
        extract_test=extract_test,
        extract_copy=extract_copy,
        extract_burst=extract_burst,
        label='HTOA',
    )
    assert result.matched is False
    assert result.burst_recovered is True
    assert result.mode == 'burst'
    assert result.test_crc == ''
    assert result.copy_crc
    assert modes == ['burst']
    assert 'recovered in burst' in (result.error or '')


def test_exhaust_without_burst_stays_unmatched(tmp_path: Path) -> None:
    test_wav = tmp_path / 't.wav'
    copy_wav = tmp_path / 'c.wav'
    good = b'RIFF' + b'\x01\x00' * 100
    bad = b'RIFF' + b'\x02\x00' * 100

    result = run_secure_test_and_copy(
        test_wav=test_wav,
        copy_wav=copy_wav,
        max_retries=0,
        burst_fallback=False,
        extract_test=lambda d: (_write(d, good), 'secure')[1],
        extract_copy=lambda d: (_write(d, bad), 'secure')[1],
        label='image',
    )
    assert result.matched is False
    assert result.burst_recovered is False
    assert result.mode == 'secure'
    assert 'CRC mismatch' in (result.error or '')


def test_reuse_initial_test_skips_first_extract(tmp_path: Path) -> None:
    test_wav = tmp_path / 't.wav'
    copy_wav = tmp_path / 'c.wav'
    pcm = b'RIFF' + b'\x10\x20' * 150
    _write(test_wav, pcm)
    test_calls = {'n': 0}

    def extract_test(dest: Path) -> str:
        test_calls['n'] += 1
        _write(dest, pcm)
        return 'secure'

    def extract_copy(dest: Path) -> str:
        _write(dest, pcm)
        return 'secure'

    result = run_secure_test_and_copy(
        test_wav=test_wav,
        copy_wav=copy_wav,
        max_retries=0,
        burst_fallback=False,
        extract_test=extract_test,
        extract_copy=extract_copy,
        reuse_initial_test=True,
        label='HTOA',
    )
    assert result.matched is True
    assert test_calls['n'] == 0


def test_copy_exception_triggers_burst_when_enabled(tmp_path: Path) -> None:
    test_wav = tmp_path / 't.wav'
    copy_wav = tmp_path / 'c.wav'
    pcm = b'RIFF' + b'\x55\x66' * 120
    _write(test_wav, pcm)

    def extract_copy(_dest: Path) -> str:
        raise RuntimeError('secure copy failed')

    def extract_burst(dest: Path) -> str:
        _write(dest, pcm)
        return 'burst'

    result = run_secure_test_and_copy(
        test_wav=test_wav,
        copy_wav=copy_wav,
        max_retries=2,
        burst_fallback=True,
        extract_test=lambda d: (_write(d, pcm), 'secure')[1],
        extract_copy=extract_copy,
        extract_burst=extract_burst,
        reuse_initial_test=True,
        label='HTOA',
    )
    assert result.burst_recovered is True
    assert result.mode == 'burst'
    assert result.test_crc == ''
    assert 'skipped test/copy CRC' in (result.error or '')
