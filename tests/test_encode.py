# SPDX-License-Identifier: GPL-3.0-or-later
"""Encoder argv / FDK probe helpers (no PyGObject)."""

from __future__ import annotations

from pathlib import Path

from ready2rip.rip.encode import (
    aac_encode_argv,
    aiff_encode_argv,
    encode_command_summary,
    encoder_tag_for_format,
    flac_encode_argv,
    prefer_fdk_aac,
    qaac_encode_argv,
    resolve_qaac_command,
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
    cmd = aac_encode_argv(Path('in.wav'), Path('out.m4a'), 'vbr5', use_fdk=False)
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


def test_qaac_encode_argv_cvbr_default_mode() -> None:
    resolve_qaac_command.cache_clear()
    cmd = qaac_encode_argv(
        Path('in.wav'),
        Path('out.m4a'),
        'cvbr256',
        command=('wine', 'C:/qaac/qaac.exe'),
    )
    assert cmd[:2] == ['wine', 'C:/qaac/qaac.exe']
    assert cmd[cmd.index('--cvbr') + 1] == '256'
    assert cmd[cmd.index('--quality') + 1] == '2'
    assert cmd[-3:] == ['-o', 'out.m4a', 'in.wav']
    resolve_qaac_command.cache_clear()


def test_qaac_encode_argv_tvbr() -> None:
    cmd = qaac_encode_argv(
        Path('in.wav'),
        Path('out.m4a'),
        'tvbr91',
        command=('qaac',),
    )
    assert cmd[0] == 'qaac'
    assert cmd[cmd.index('--tvbr') + 1] == '91'
    assert '--cvbr' not in cmd


def test_qaac_encode_argv_cbr() -> None:
    cmd = qaac_encode_argv(
        Path('in.wav'),
        Path('out.m4a'),
        'cbr256',
        command=('qaac',),
    )
    assert cmd[cmd.index('--cbr') + 1] == '256'


def test_qaac_summary() -> None:
    text = encode_command_summary('qaac', qaac_quality='cvbr256')
    assert 'qaac' in text.lower()
    assert 'CVBR' in text or 'cvbr' in text.lower()


def test_resolve_qaac_finds_local_bin_without_path(monkeypatch, tmp_path: Path) -> None:
    """GUI / AppImage PATH often omits ~/.local/bin — still find the wrapper."""
    from ready2rip.rip import encode as encode_mod

    encode_mod.resolve_qaac_command.cache_clear()
    home = tmp_path / 'home'
    local_bin = home / '.local' / 'bin'
    local_bin.mkdir(parents=True)
    wrapper = local_bin / 'qaac'
    wrapper.write_text('#!/bin/sh\nexit 0\n', encoding='utf-8')
    wrapper.chmod(0o755)

    monkeypatch.setenv('HOME', str(home))
    monkeypatch.delenv('QAAC', raising=False)
    monkeypatch.delenv('READY2RIP_QAAC', raising=False)
    monkeypatch.delenv('XDG_BIN_HOME', raising=False)
    monkeypatch.setattr(encode_mod.shutil, 'which', lambda _name: None)

    cmd = encode_mod.resolve_qaac_command()
    assert cmd == (str(wrapper),)
    encode_mod.resolve_qaac_command.cache_clear()


def test_encoder_tag_for_wav_and_aac(monkeypatch) -> None:
    from ready2rip.rip import encode as encode_mod

    encode_mod.encoder_tag_for_format.cache_clear()
    monkeypatch.setattr(encode_mod, '_ffmpeg_version_label', lambda: 'ffmpeg 7.0.2')
    monkeypatch.setattr(encode_mod, 'prefer_fdk_aac', lambda: True)
    assert encoder_tag_for_format('wav') == 'WAV (PCM)'
    assert encoder_tag_for_format('aac') == 'ffmpeg 7.0.2 (libfdk_aac)'
    encode_mod.encoder_tag_for_format.cache_clear()


def test_resolve_qaac_uses_qaac_wine_prefix(monkeypatch, tmp_path: Path) -> None:
    from ready2rip.rip import encode as encode_mod

    encode_mod.resolve_qaac_command.cache_clear()
    home = tmp_path / 'home'
    exe = home / '.local' / 'share' / 'qaac-wine' / 'prefix' / 'drive_c' / 'qaac' / 'qaac64.exe'
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b'MZ')

    monkeypatch.setenv('HOME', str(home))
    monkeypatch.delenv('QAAC', raising=False)
    monkeypatch.delenv('READY2RIP_QAAC', raising=False)
    monkeypatch.delenv('XDG_BIN_HOME', raising=False)

    def fake_which(name: str) -> str | None:
        return '/usr/bin/wine' if name == 'wine' else None

    monkeypatch.setattr(encode_mod.shutil, 'which', fake_which)

    cmd = encode_mod.resolve_qaac_command()
    assert cmd is not None
    assert cmd[0] == 'env'
    assert cmd[1].startswith('WINEPREFIX=')
    assert cmd[2] == '/usr/bin/wine'
    assert cmd[3] == str(exe)
    encode_mod.resolve_qaac_command.cache_clear()
