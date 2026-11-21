# SPDX-License-Identifier: GPL-3.0-or-later
"""Archival secure→burst gate hardening."""

from __future__ import annotations

from pathlib import Path

from ready2rip.rip.engine_extract import ExtractMixin
from ready2rip.rip.paranoia_stats import ParanoiaStats


def test_nonzero_exit_with_skips_needs_burst(tmp_path: Path) -> None:
    wav = tmp_path / 't.wav'
    wav.write_bytes(b'RIFF' + b'\0' * 5000)
    stats = ParanoiaStats(exit_code=1, skips=2, finished=True)
    assert ExtractMixin._secure_pass_needs_burst(stats, wav, 10_000) is True


def test_exit_zero_with_hard_errors_needs_burst(tmp_path: Path) -> None:
    wav = tmp_path / 't.wav'
    wav.write_bytes(b'RIFF' + b'\0' * 5000)
    stats = ParanoiaStats(exit_code=0, readerrs=1, finished=True)
    assert ExtractMixin._secure_pass_needs_burst(stats, wav, 10_000) is True


def test_clean_exit_zero_does_not_need_burst(tmp_path: Path) -> None:
    wav = tmp_path / 't.wav'
    wav.write_bytes(b'RIFF' + b'\0' * 5000)
    stats = ParanoiaStats(exit_code=0, finished=True)
    assert ExtractMixin._secure_pass_needs_burst(stats, wav, 10_000) is False


def test_cacheerrs_count_as_hard(tmp_path: Path) -> None:
    wav = tmp_path / 't.wav'
    wav.write_bytes(b'RIFF' + b'\0' * 5000)
    stats = ParanoiaStats(exit_code=1, cacheerrs=3, finished=False)
    assert ExtractMixin._secure_pass_needs_burst(stats, wav, 10_000) is True
