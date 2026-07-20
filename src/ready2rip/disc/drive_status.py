# SPDX-License-Identifier: GPL-3.0-or-later
"""Optical drive tray / media status (Linux CDROM ioctls) and eject."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from enum import Enum

log = logging.getLogger(__name__)

# From linux/cdrom.h
_CDROM_DRIVE_STATUS = 0x5326
_CDS_NO_INFO = 0
_CDS_NO_DISC = 1
_CDS_TRAY_OPEN = 2
_CDS_DRIVE_NOT_READY = 3
_CDS_DISC_OK = 4


class DriveTrayState(Enum):
    """High-level optical drive / tray state."""

    UNKNOWN = 'unknown'
    TRAY_OPEN = 'tray_open'
    NO_DISC = 'no_disc'
    NOT_READY = 'not_ready'
    DISC_OK = 'disc_ok'
    MISSING = 'missing'  # device node missing / cannot open


@dataclass(frozen=True)
class DriveStatus:
    device: str
    state: DriveTrayState
    raw_code: int | None = None
    message: str = ''

    @property
    def tray_open(self) -> bool:
        return self.state is DriveTrayState.TRAY_OPEN

    @property
    def has_media(self) -> bool:
        return self.state is DriveTrayState.DISC_OK

    @property
    def label(self) -> str:
        return {
            DriveTrayState.UNKNOWN: 'Unknown',
            DriveTrayState.TRAY_OPEN: 'Tray open',
            DriveTrayState.NO_DISC: 'No disc',
            DriveTrayState.NOT_READY: 'Drive not ready',
            DriveTrayState.DISC_OK: 'Disc ready',
            DriveTrayState.MISSING: 'Drive not found',
        }.get(self.state, 'Unknown')


def query_drive_status(device: str = '/dev/sr0') -> DriveStatus:
    """Return tray/media status for *device* via ``CDROM_DRIVE_STATUS``."""
    from ready2rip.util import validate_device_path

    if not device:
        return DriveStatus(
            device=device or '',
            state=DriveTrayState.MISSING,
            message='No device configured',
        )
    try:
        device = validate_device_path(device)
    except ValueError as exc:
        return DriveStatus(
            device=device,
            state=DriveTrayState.MISSING,
            message=str(exc),
        )

    try:
        fd = os.open(device, os.O_RDONLY | os.O_NONBLOCK)
    except OSError as exc:
        return DriveStatus(
            device=device,
            state=DriveTrayState.MISSING,
            message=str(exc),
        )

    try:
        import fcntl

        # Third arg must be int for CDROM_DRIVE_STATUS on Linux.
        code = fcntl.ioctl(fd, _CDROM_DRIVE_STATUS, 0)
    except OSError as exc:
        log.debug('CDROM_DRIVE_STATUS failed on %s: %s', device, exc)
        return DriveStatus(
            device=device,
            state=DriveTrayState.UNKNOWN,
            message=str(exc),
        )
    finally:
        try:
            os.close(fd)
        except OSError:
            pass

    state = {
        _CDS_NO_INFO: DriveTrayState.UNKNOWN,
        _CDS_NO_DISC: DriveTrayState.NO_DISC,
        _CDS_TRAY_OPEN: DriveTrayState.TRAY_OPEN,
        _CDS_DRIVE_NOT_READY: DriveTrayState.NOT_READY,
        _CDS_DISC_OK: DriveTrayState.DISC_OK,
    }.get(code, DriveTrayState.UNKNOWN)

    return DriveStatus(device=device, state=state, raw_code=code, message=state.value)


def eject_drive(device: str = '/dev/sr0') -> tuple[bool, str]:
    """Eject the tray. Prefer ``eject``, fall back to CDROMEJECT ioctl."""
    from ready2rip.util import validate_device_path

    try:
        device = validate_device_path(device)
    except ValueError as exc:
        return False, str(exc)

    eject_bin = shutil.which('eject')
    if eject_bin:
        try:
            completed = subprocess.run(
                [eject_bin, '--', device],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, str(exc)
        if completed.returncode == 0:
            return True, f'Ejected {device}'
        detail = (completed.stderr or completed.stdout or '').strip()
        if detail:
            return False, detail

    # Fallback ioctl
    CDROMEJECT = 0x5309
    try:
        fd = os.open(device, os.O_RDONLY | os.O_NONBLOCK)
    except OSError as exc:
        return False, str(exc)
    try:
        import fcntl

        fcntl.ioctl(fd, CDROMEJECT, 0)
        return True, f'Ejected {device} (ioctl)'
    except OSError as exc:
        return False, str(exc)
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
