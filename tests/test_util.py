# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for device / URL safety helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from ready2rip.util import (
    argv_primary_executable,
    is_safe_http_url,
    path_is_under_appdir,
    subprocess_env_for,
    validate_device_path,
)


def test_validate_device_path_optical() -> None:
    assert validate_device_path('/dev/sr0') == '/dev/sr0'
    assert validate_device_path('/dev/sr1') == '/dev/sr1'
    with pytest.raises(ValueError):
        validate_device_path('/dev/sda')
    with pytest.raises(ValueError):
        validate_device_path('/tmp/evil')
    with pytest.raises(ValueError):
        validate_device_path('/dev/sr0;rm -rf /')


def test_is_safe_http_url_rejects_unsafe() -> None:
    assert not is_safe_http_url('https://evil.example.com/')
    assert not is_safe_http_url('http://127.0.0.1/')
    assert not is_safe_http_url('https://localhost/x')
    assert not is_safe_http_url('ftp://musicbrainz.org/')
    assert not is_safe_http_url('https://8.8.8.8/')


def test_is_safe_http_url_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        'ready2rip.util.socket.getaddrinfo',
        lambda *_a, **_k: [(0, 0, 0, '', ('93.184.216.34', 0))],
    )
    assert is_safe_http_url('https://musicbrainz.org/ws/2/release')
    assert is_safe_http_url('http://www.accuraterip.com/accuraterip/x.bin')
    assert is_safe_http_url('https://db.cue.tools/lookup2.php?toc=0:1')
    assert is_safe_http_url('http://db.cuetools.net/lookup2.php?toc=0:1')


def test_is_safe_http_url_rejects_private_resolve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        'ready2rip.util.socket.getaddrinfo',
        lambda *_a, **_k: [(0, 0, 0, '', ('10.0.0.1', 0))],
    )
    assert not is_safe_http_url('https://musicbrainz.org/ws/2/release')


def test_argv_primary_executable_skips_env_and_stdbuf() -> None:
    assert argv_primary_executable(['flac', '-5', 'a.wav']) == 'flac'
    assert (
        argv_primary_executable(
            ['env', 'WINEPREFIX=/tmp/w', 'wine', 'qaac64.exe', '-o', 'x.m4a']
        )
        == 'wine'
    )
    assert argv_primary_executable(['stdbuf', '-oL', '-eL', 'cdparanoia', '-Q']) == (
        'cdparanoia'
    )


def test_subprocess_env_for_scrubs_host_tools(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    appdir = tmp_path / 'AppDir'
    bundled = appdir / 'usr' / 'bin' / 'flac'
    bundled.parent.mkdir(parents=True)
    bundled.write_text('#!/bin/sh\n')
    bundled.chmod(0o755)
    host_bin = tmp_path / 'host' / 'qaac'
    host_bin.parent.mkdir(parents=True)
    host_bin.write_text('#!/bin/sh\n')
    host_bin.chmod(0o755)

    monkeypatch.setenv('APPDIR', str(appdir))
    monkeypatch.setenv('LD_LIBRARY_PATH', f'{appdir}/usr/lib:/usr/lib')
    monkeypatch.setenv('PYTHONHOME', str(appdir / 'usr'))
    monkeypatch.setenv('GI_TYPELIB_PATH', f'{appdir}/usr/lib/girepository-1.0')

    host_env = subprocess_env_for([str(host_bin), '--tvbr', '90'])
    assert host_env.get('LD_LIBRARY_PATH') == '/usr/lib'
    assert 'PYTHONHOME' not in host_env
    assert 'GI_TYPELIB_PATH' not in host_env

    bundled_env = subprocess_env_for([str(bundled), '-5', 'x.wav'])
    assert f'{appdir}/usr/lib' in bundled_env.get('LD_LIBRARY_PATH', '')
    assert bundled_env.get('PYTHONHOME') == str(appdir / 'usr')


def test_path_is_under_appdir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    appdir = tmp_path / 'AppDir'
    nested = appdir / 'usr' / 'bin' / 'flac'
    nested.parent.mkdir(parents=True)
    nested.write_text('x')
    outside = tmp_path / 'other'
    outside.write_text('y')
    monkeypatch.setenv('APPDIR', str(appdir))
    assert path_is_under_appdir(nested)
    assert not path_is_under_appdir(outside)


def test_subprocess_env_for_noop_without_appdir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv('APPDIR', raising=False)
    monkeypatch.setenv('LD_LIBRARY_PATH', '/opt/foo')
    env = subprocess_env_for(['/bin/bash', '-c', 'true'])
    assert env.get('LD_LIBRARY_PATH') == '/opt/foo'
