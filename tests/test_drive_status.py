# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for optical eject retry / ioctl fallback."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from ready2rip.disc.drive_status import (
    _is_retryable_eject_error,
    eject_drive,
)


def test_retryable_eject_error_detects_busy() -> None:
    assert _is_retryable_eject_error('Device or resource busy')
    assert _is_retryable_eject_error('drive not ready')
    assert not _is_retryable_eject_error('Permission denied')


def test_eject_falls_back_to_ioctl_when_cli_fails_with_stderr() -> None:
    """CLI eject used to return early on any stderr and skip ioctl."""
    failed = MagicMock(returncode=1, stderr='Device or resource busy\n', stdout='')
    with (
        patch('ready2rip.disc.drive_status.shutil.which', return_value='/usr/bin/eject'),
        patch('ready2rip.disc.drive_status.subprocess.run', return_value=failed),
        patch('ready2rip.disc.drive_status.os.open', return_value=7),
        patch('ready2rip.disc.drive_status.os.close'),
        patch('fcntl.ioctl') as ioctl,
        patch('ready2rip.disc.drive_status.time.sleep'),
    ):
        ok, message = eject_drive('/dev/sr0', attempts=1, delay_s=0)
    assert ok
    assert 'ioctl' in message
    ioctl.assert_called_once()


def test_eject_retries_then_succeeds_via_cli() -> None:
    busy = MagicMock(returncode=1, stderr='Device or resource busy', stdout='')
    good = MagicMock(returncode=0, stderr='', stdout='')
    ioctl_err = OSError('Device or resource busy')

    with (
        patch('ready2rip.disc.drive_status.shutil.which', return_value='/usr/bin/eject'),
        patch(
            'ready2rip.disc.drive_status.subprocess.run',
            side_effect=[busy, good],
        ) as run,
        patch('ready2rip.disc.drive_status.os.open', return_value=7),
        patch('ready2rip.disc.drive_status.os.close'),
        patch('fcntl.ioctl', side_effect=ioctl_err),
        patch('ready2rip.disc.drive_status.time.sleep') as sleep,
    ):
        ok, message = eject_drive('/dev/sr0', attempts=3, delay_s=0.5)

    assert ok
    assert 'ioctl' not in message
    assert run.call_count == 2
    sleep.assert_called()
