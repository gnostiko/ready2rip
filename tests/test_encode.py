# SPDX-License-Identifier: GPL-3.0-or-later
"""Encoder argv / FDK probe helpers (no PyGObject)."""

from __future__ import annotations

from pathlib import Path

from ready2rip.rip.encode import (
    aac_encode_argv,
    aiff_encode_argv,
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


def test_native_aac_vbr_uses_quality_scale_not_cbr() -> None:
    cmd = aac_encode_argv(
        Path('in.wav'), Path('out.m4a'), 'vbr5', use_fdk=False
    )
    assert 'aac' in cmd
    assert '-q:a' in cmd
    assert '-b:a' not in cmd
    q = float(cmd[cmd.index('-q:a') + 1])
    assert q >= 1.0


def test_aiff_encode_argv_is_pcm_be() -> None:
    cmd = aiff_encode_argv(Path('in.wav'), Path('out.aiff'))
    assert cmd[0] == 'ffmpeg'
    assert 'pcm_s16be' in cmd
    assert 'aiff' in cmd
    assert cmd[-1] == 'out.aiff'


def test_aiff_summary() -> None:
    assert 'aiff' in encode_command_summary('aiff').lower()
