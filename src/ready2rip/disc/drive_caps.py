# SPDX-License-Identifier: GPL-3.0-or-later
"""Probe optical-drive overread / cache behaviour (no C2 usage yet)."""

from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ready2rip.disc.probe import DiscInfo
from ready2rip.util import find_cdparanoia

log = logging.getLogger(__name__)


@dataclass
class DriveCapabilities:
    """Measured capabilities for the current disc/drive pair."""

    can_overread_leadout: bool = False
    can_overread_leadin: bool = False
    note: str = ''

    @property
    def can_overread(self) -> bool:
        return self.can_overread_leadout or self.can_overread_leadin


def probe_drive_capabilities(
    device: str,
    disc: DiscInfo | None,
    *,
    timeout: float = 45.0,
) -> DriveCapabilities:
    """Try short reads past TOC edges; return what the drive tolerates."""
    caps = DriveCapabilities()
    if disc is None or not disc.tracks:
        caps.note = 'No disc TOC — capabilities not probed'
        return caps
    binary = find_cdparanoia()
    if binary is None:
        caps.note = 'cdparanoia not found — capabilities not probed'
        return caps

    first = disc.tracks[0]
    last = disc.tracks[-1]
    leadout = int(last.start_sector) + int(last.length_sectors)
    if int(getattr(disc, 'leadout_lba', 0) or 0) > leadout:
        leadout = int(disc.leadout_lba)

    with tempfile.TemporaryDirectory(prefix='ready2rip-caps-') as tmp:
        tmp_path = Path(tmp)
        # Lead-out: one sector past audio end.
        out_wav = tmp_path / 'leadout.wav'
        caps.can_overread_leadout = _try_span(
            binary, device, leadout, leadout + 1, out_wav, timeout=timeout
        )
        # Lead-in: one sector before track 1 (may be negative LBA).
        in_start = int(first.start_sector) - 1
        in_wav = tmp_path / 'leadin.wav'
        caps.can_overread_leadin = _try_span(
            binary, device, in_start, in_start + 1, in_wav, timeout=timeout
        )

    bits: list[str] = []
    if caps.can_overread_leadout:
        bits.append('lead-out OK')
    else:
        bits.append('lead-out failed')
    if caps.can_overread_leadin:
        bits.append('lead-in OK')
    else:
        bits.append('lead-in failed')
    caps.note = 'Overread: ' + ', '.join(bits)
    log.info('Drive capabilities on %s: %s', device, caps.note)
    return caps


def _try_span(
    binary: str,
    device: str,
    start: int,
    end: int,
    wav_path: Path,
    *,
    timeout: float,
) -> bool:
    import subprocess

    if end <= start:
        return False
    rel = end - start - 1
    span = f'[.{start}]-[.{rel}]'
    cmd = [binary, '-w', '-d', device, '-Z', '-e', span, str(wav_path)]
    try:
        proc = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.debug('Capability span %s failed: %s', span, exc)
        return False
    if not wav_path.is_file():
        return False
    try:
        size = wav_path.stat().st_size
    except OSError:
        return False
    # WAV header + at least part of one CDDA sector.
    ok = size >= 44 + 1000 and proc.returncode == 0
    if not ok:
        log.debug(
            'Capability span %s rc=%s size=%s', span, proc.returncode, size
        )
    return ok
