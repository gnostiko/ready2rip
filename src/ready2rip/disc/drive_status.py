# SPDX-License-Identifier: GPL-3.0-or-later
"""Optical drive tray / media status (Linux CDROM ioctls) and eject."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from enum import Enum

from ready2rip.util import subprocess_env_for

log = logging.getLogger(__name__)

# From linux/cdrom.h
_CDROM_DRIVE_STATUS = 0x5326
_CDS_NO_INFO = 0
_CDS_NO_DISC = 1
_CDS_TRAY_OPEN = 2
_CDS_DRIVE_NOT_READY = 3
_CDS_DISC_OK = 4
_CDROMEJECT = 0x5309

# After a rip the kernel / drive may still hold the device briefly.
_EJECT_ATTEMPTS = 6
_EJECT_RETRY_DELAY_S = 0.75


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
    message: str = ''

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

    return DriveStatus(device=device, state=state, message=state.value)


def _is_retryable_eject_error(message: str) -> bool:
    """True when a short wait is likely to make eject succeed."""
    text = (message or '').casefold()
    needles = (
        'busy',
        'resource temporarily unavailable',
        'device or resource busy',
        'not ready',
        'medium not present',  # transient while settling
        'no medium found',
    )
    return any(n in text for n in needles)


def _eject_via_cli(device: str) -> tuple[bool, str]:
    """Run util-linux ``eject`` when available."""
    eject_bin = shutil.which('eject')
    if not eject_bin:
        return False, ''
    try:
        eject_cmd = [eject_bin, '--', device]
        completed = subprocess.run(
            eject_cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
            env=subprocess_env_for(eject_cmd),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    if completed.returncode == 0:
        return True, f'Ejected {device}'
    detail = (completed.stderr or completed.stdout or '').strip()
    return False, detail or f'eject exited {completed.returncode}'


def _eject_via_ioctl(device: str) -> tuple[bool, str]:
    """Open the tray with ``CDROMEJECT``."""
    try:
        fd = os.open(device, os.O_RDONLY | os.O_NONBLOCK)
    except OSError as exc:
        return False, str(exc)
    try:
        import fcntl

        fcntl.ioctl(fd, _CDROMEJECT, 0)
        return True, f'Ejected {device} (ioctl)'
    except OSError as exc:
        return False, str(exc)
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def eject_drive(
    device: str = '/dev/sr0',
    *,
    attempts: int = _EJECT_ATTEMPTS,
    delay_s: float = _EJECT_RETRY_DELAY_S,
) -> tuple[bool, str]:
    """Eject the tray. Prefer ``eject``, fall back to CDROMEJECT ioctl.

    Retries on transient “device busy / not ready” errors — common right after
    a rip while the kernel still has the optical device open.
    """
    from ready2rip.util import validate_device_path

    try:
        device = validate_device_path(device)
    except ValueError as exc:
        return False, str(exc)

    tries = max(1, int(attempts))
    last_err = 'Eject failed'
    for attempt in range(tries):
        if attempt > 0:
            time.sleep(max(0.0, float(delay_s)))

        retryable = False
        ok, msg = _eject_via_cli(device)
        if ok:
            return True, msg
        if msg:
            last_err = msg
            retryable = retryable or _is_retryable_eject_error(msg)
            log.debug('eject CLI failed on %s (try %s/%s): %s', device, attempt + 1, tries, msg)

        # Always try ioctl when CLI is missing or failed.
        ok, msg = _eject_via_ioctl(device)
        if ok:
            return True, msg
        if msg:
            last_err = msg
            retryable = retryable or _is_retryable_eject_error(msg)
            log.debug('eject ioctl failed on %s (try %s/%s): %s', device, attempt + 1, tries, msg)

        if attempt + 1 < tries and not retryable:
            # Permanent failure (permissions, missing node, …) — stop early.
            break

    return False, last_err


def empty_tray_copy(state: DriveTrayState, device: str) -> tuple[str, str] | None:
    """Status-page title and description for an empty or missing drive."""
    if state is DriveTrayState.TRAY_OPEN:
        return (
            'Tray open',
            f'The drive tray is open on {device}. Insert a disc and close the tray.',
        )
    if state is DriveTrayState.NO_DISC:
        return (
            'No disc',
            f'Tray is closed but no disc is in {device}. Insert an audio CD.',
        )
    if state is DriveTrayState.MISSING:
        return (
            'Drive not found',
            f'Cannot open {device}. Check the optical device path in Settings.',
        )
    return None
