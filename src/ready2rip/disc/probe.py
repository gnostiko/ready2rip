# SPDX-License-Identifier: GPL-3.0-or-later
"""Probe an optical drive for an audio CD using cdparanoia."""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field


@dataclass(frozen=True)
class TrackInfo:
    """One audio track from the disc TOC."""

    number: int
    length_sectors: int
    start_sector: int = 0

    @property
    def duration_seconds(self) -> float:
        # CDDA: 75 sectors per second
        return self.length_sectors / 75.0

    @property
    def duration_label(self) -> str:
        total = int(round(self.duration_seconds))
        minutes, seconds = divmod(total, 60)
        return f'{minutes}:{seconds:02d}'


@dataclass
class DiscInfo:
    """Summary of an inserted audio CD."""

    device: str
    tracks: list[TrackInfo] = field(default_factory=list)
    freedb_id: str | None = None
    raw_output: str = ''

    @property
    def track_count(self) -> int:
        return len(self.tracks)


# cdparanoia -Q lines look like:
#   1.     15032 [03:20.32]        0 [00:00.00]    no   no  2
_TRACK_LINE_RE = re.compile(
    r'^\s*(\d+)\.\s+(\d+)\s+\[(\d+):(\d+)\.(\d+)\]\s+(\d+)',
    re.MULTILINE,
)
_FREEDB_RE = re.compile(r'CDDB discid:\s*([0-9a-fA-F]+)', re.IGNORECASE)


def probe_disc(device: str = '/dev/sr0') -> DiscInfo | None:
    """Query *device* with ``cdparanoia -Q`` and parse the TOC.

    Returns ``None`` if cdparanoia is missing, the device cannot be read,
    or no audio tracks are found.
    """
    from ready2rip.util import find_cdparanoia, validate_device_path

    try:
        device = validate_device_path(device)
    except ValueError:
        return None

    cdparanoia = find_cdparanoia()
    if not cdparanoia:
        return None

    try:
        completed = subprocess.run(
            [cdparanoia, '-Q', '-d', device],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None

    # cdparanoia prints the TOC on stderr.
    output = (completed.stderr or '') + '\n' + (completed.stdout or '')
    tracks = _parse_tracks(output)
    if not tracks:
        return None

    freedb = None
    match = _FREEDB_RE.search(output)
    if match:
        freedb = match.group(1).lower()

    return DiscInfo(
        device=device,
        tracks=tracks,
        freedb_id=freedb,
        raw_output=output,
    )


def _parse_tracks(output: str) -> list[TrackInfo]:
    tracks: list[TrackInfo] = []
    for match in _TRACK_LINE_RE.finditer(output):
        number = int(match.group(1))
        length = int(match.group(2))
        start = int(match.group(6))
        tracks.append(
            TrackInfo(
                number=number,
                length_sectors=length,
                start_sector=start,
            )
        )
    return tracks
