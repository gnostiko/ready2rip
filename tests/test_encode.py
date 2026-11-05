# SPDX-License-Identifier: GPL-3.0-or-later
"""Encoder argv / FDK probe helpers (no PyGObject)."""

from __future__ import annotations

from pathlib import Path

from ready2rip.rip.encode import (
    encode_command_summary,
    flac_encode_argv,
    prefer_fdk_aac,
)


def test_flac_encode_argv_includes_verify_by_default() -> None:
    cmd = flac_encode_argv(Path('in.wav'), Path('out.flac'), 8)
    assert cmd[:3] == ['flac', '-8', '--silent']
    assert '--verify' in cmd
    assert cmd[-3:] == ['-o', 'out.flac', 'in.wav']


def test_flac_encode_argv_can_skip_verify() -> None:
    cmd = flac_encode_argv(Path('in.wav'), Path('out.flac'), 5, verify=False)
    assert '--verify' not in cmd
    assert '-5' in cmd


def test_flac_summary_mentions_verify() -> None:
    text = encode_command_summary('flac', flac_compression=8)
    assert '-8' in text
    assert '--verify' in text


def test_prefer_fdk_aac_is_cached() -> None:
    prefer_fdk_aac.cache_clear()
    first = prefer_fdk_aac()
    second = prefer_fdk_aac()
    assert first is second
    info = prefer_fdk_aac.cache_info()
    assert info.hits >= 1
    prefer_fdk_aac.cache_clear()
