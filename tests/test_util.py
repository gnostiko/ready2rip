# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for device / URL safety helpers."""

from __future__ import annotations

import pytest

from ready2rip.util import is_safe_http_url, validate_device_path


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
