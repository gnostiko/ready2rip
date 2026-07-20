# SPDX-License-Identifier: GPL-3.0-or-later
"""Optical drive identification for the Drive sidebar panel."""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class DriveInfo:
    """Technical summary of an optical drive."""

    device: str
    vendor: str = ''
    model: str = ''
    revision: str = ''
    serial: str = ''
    bus: str = ''
    transport: str = ''
    capabilities: list[str] = field(default_factory=list)
    speed: str = ''
    notes: list[str] = field(default_factory=list)

    @property
    def display_name(self) -> str:
        parts = [p for p in (self.vendor, self.model) if p]
        return ' '.join(parts) if parts else self.device

    def as_rows(self) -> list[tuple[str, str]]:
        """Return (title, subtitle) pairs for Adw.ActionRow."""
        rows: list[tuple[str, str]] = [
            ('Device', self.device),
        ]
        if self.display_name and self.display_name != self.device:
            rows.append(('Model', self.display_name))
        if self.revision:
            rows.append(('Firmware', self.revision))
        if self.serial:
            rows.append(('Serial', self.serial))
        if self.bus or self.transport:
            rows.append(('Bus', ' · '.join(p for p in (self.bus, self.transport) if p)))
        if self.speed:
            rows.append(('Drive speed', self.speed))
        if self.capabilities:
            rows.append(('Capabilities', ', '.join(self.capabilities)))
        for note in self.notes:
            rows.append(('Note', note))
        return rows


def probe_drive(device: str = '/dev/sr0') -> DriveInfo:
    """Collect drive identity from udev, sysfs, and optional cd-drive."""
    info = DriveInfo(device=device)
    _fill_from_udev(info)
    _fill_from_sysfs(info)
    _fill_from_proc_cdrom(info)
    if not info.model and not info.vendor:
        _fill_from_cd_drive(info)
    if not info.model and not info.vendor:
        _fill_from_cdparanoia(info)
    return info


def _fill_from_udev(info: DriveInfo) -> None:
    udevadm = shutil.which('udevadm')
    if not udevadm:
        return
    try:
        completed = subprocess.run(
            [udevadm, 'info', '--query=property', f'--name={info.device}'],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return

    props: dict[str, str] = {}
    for line in (completed.stdout or '').splitlines():
        if '=' in line:
            key, val = line.split('=', 1)
            props[key] = val

    model = props.get('ID_MODEL') or props.get('ID_MODEL_FROM_DATABASE') or ''
    model = model.replace('_', ' ').strip()
    # Often "HL-DT-ST DVDRAM GUE1N" is entirely in MODEL.
    vendor = (props.get('ID_VENDOR') or props.get('ID_VENDOR_FROM_DATABASE') or '').replace(
        '_', ' '
    ).strip()
    if model and not vendor and ' ' in model:
        # Split common "VENDOR MODEL…" prefixes.
        first, rest = model.split(None, 1)
        if len(first) <= 12:
            vendor, model = first, rest

    info.vendor = info.vendor or vendor
    info.model = info.model or model
    info.revision = info.revision or (props.get('ID_REVISION') or '').strip()
    info.serial = info.serial or (
        props.get('ID_SERIAL_SHORT') or props.get('ID_SERIAL') or ''
    ).strip()
    info.bus = info.bus or (props.get('ID_BUS') or '').upper()
    if props.get('ID_ATA_SATA') == '1':
        info.transport = info.transport or 'SATA'
    elif props.get('ID_USB_DRIVER'):
        info.transport = info.transport or 'USB'

    caps: list[str] = []
    flag_map = (
        ('ID_CDROM_CD', 'CD'),
        ('ID_CDROM_CD_R', 'CD-R'),
        ('ID_CDROM_CD_RW', 'CD-RW'),
        ('ID_CDROM_DVD', 'DVD'),
        ('ID_CDROM_DVD_R', 'DVD-R'),
        ('ID_CDROM_DVD_RW', 'DVD-RW'),
        ('ID_CDROM_DVD_RAM', 'DVD-RAM'),
        ('ID_CDROM_DVD_PLUS_R', 'DVD+R'),
        ('ID_CDROM_DVD_PLUS_RW', 'DVD+RW'),
        ('ID_CDROM_BD', 'Blu-ray'),
    )
    for key, label in flag_map:
        if props.get(key) == '1' and label not in caps:
            caps.append(label)
    if caps:
        info.capabilities = caps


def _fill_from_sysfs(info: DriveInfo) -> None:
    dev = Path(info.device)
    name = dev.name  # sr0
    sys_block = Path('/sys/block') / name
    if not sys_block.is_dir():
        return
    # vendor/model via device/
    for key, attr in (
        ('vendor', 'device/vendor'),
        ('model', 'device/model'),
        ('revision', 'device/rev'),
    ):
        path = sys_block / attr
        try:
            value = path.read_text(encoding='utf-8', errors='replace').strip()
        except OSError:
            continue
        if value and not getattr(info, key):
            setattr(info, key, value)


def _fill_from_proc_cdrom(info: DriveInfo) -> None:
    path = Path('/proc/sys/dev/cdrom/info')
    if not path.is_file():
        return
    try:
        text = path.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return

    # Find column for our drive name.
    names_m = re.search(r'^drive name:\s*(.+)$', text, re.MULTILINE)
    speed_m = re.search(r'^drive speed:\s*(.+)$', text, re.MULTILINE)
    if not names_m:
        return
    names = names_m.group(1).split()
    speeds = speed_m.group(1).split() if speed_m else []
    dev_name = Path(info.device).name
    try:
        idx = names.index(dev_name)
    except ValueError:
        idx = 0 if len(names) == 1 else -1
    if idx >= 0 and idx < len(speeds):
        info.speed = f'{speeds[idx]}×'


def _fill_from_cd_drive(info: DriveInfo) -> None:
    cd_drive = shutil.which('cd-drive')
    if not cd_drive:
        return
    try:
        completed = subprocess.run(
            [cd_drive, '-q', info.device],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return
    text = (completed.stdout or '') + '\n' + (completed.stderr or '')
    for line in text.splitlines():
        lower = line.lower()
        if 'vendor' in lower and ':' in line and not info.vendor:
            info.vendor = line.split(':', 1)[1].strip()
        elif 'model' in lower and ':' in line and not info.model:
            info.model = line.split(':', 1)[1].strip()
        elif 'revision' in lower and ':' in line and not info.revision:
            info.revision = line.split(':', 1)[1].strip()


def _fill_from_cdparanoia(info: DriveInfo) -> None:
    cdparanoia = shutil.which('cdparanoia')
    if not cdparanoia:
        return
    try:
        completed = subprocess.run(
            [cdparanoia, '-Q', '-d', info.device],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return
    text = (completed.stderr or '') + '\n' + (completed.stdout or '')
    # "CDROM sensed: HL-DT-ST DVDRAM GUE1N     ME03 SCSI CD-ROM"
    match = re.search(r'CDROM sensed:\s*(.+)', text)
    if match and not info.model:
        sensed = match.group(1).strip()
        # Drop trailing "SCSI CD-ROM"
        sensed = re.sub(r'\s+SCSI\s+CD-ROM\s*$', '', sensed, flags=re.I)
        parts = sensed.split()
        if len(parts) >= 2:
            # last token often firmware
            if re.fullmatch(r'[A-Z0-9]{2,6}', parts[-1]):
                info.revision = info.revision or parts[-1]
                parts = parts[:-1]
            info.vendor = info.vendor or parts[0]
            info.model = info.model or ' '.join(parts[1:])
        else:
            info.model = sensed
