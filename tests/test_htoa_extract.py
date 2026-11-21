# SPDX-License-Identifier: GPL-3.0-or-later
"""HTOA extract uses the same mode→paranoia flags as track extract."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from ready2rip.disc.htoa import HtoaInfo, extract_htoa
from ready2rip.rip.secure import (
    burst_paranoia_flags,
    normal_paranoia_flags,
    secure_paranoia_flags,
)


def _htoa(sectors: int = 150) -> HtoaInfo:
    return HtoaInfo(start_sector=0, length_sectors=sectors)


def _capture_cmd(tmp_path: Path, *, mode: str) -> list[str]:
    wav = tmp_path / 'htoa.wav'
    wav.write_bytes(b'RIFF' + b'\0' * 2000)
    cmds: list[list[str]] = []

    def fake_run(cmd, **_kwargs):
        cmds.append(list(cmd))
        result = MagicMock()
        result.returncode = 0
        result.stderr = ''
        result.stdout = ''
        return result

    with (
        patch('ready2rip.util.find_cdparanoia', return_value='cdparanoia'),
        patch('ready2rip.disc.htoa.subprocess.run', side_effect=fake_run),
    ):
        extract_htoa('/dev/sr0', _htoa(), wav, mode=mode)

    assert cmds, 'subprocess.run was not called'
    return cmds[0]


def test_htoa_secure_uses_abort_on_skip(tmp_path: Path) -> None:
    cmd = _capture_cmd(tmp_path, mode='secure')
    for flag in secure_paranoia_flags():
        assert flag in cmd
    assert '-Z' not in cmd


def test_htoa_normal_allows_repair(tmp_path: Path) -> None:
    cmd = _capture_cmd(tmp_path, mode='normal')
    for flag in normal_paranoia_flags():
        assert flag in cmd
    assert '-X' not in cmd
    assert '-Z' not in cmd


def test_htoa_burst_disables_paranoia_quietly(tmp_path: Path) -> None:
    cmd = _capture_cmd(tmp_path, mode='burst')
    for flag in burst_paranoia_flags():
        assert flag in cmd
    assert '-q' in cmd
    assert '-X' not in cmd
