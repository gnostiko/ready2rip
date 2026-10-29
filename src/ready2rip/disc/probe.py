# SPDX-License-Identifier: GPL-3.0-or-later
"""Probe an optical drive for an audio CD using cdparanoia."""

from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass, field, replace

log = logging.getLogger(__name__)


def sectors_to_mmss(sectors: int) -> str:
    """Format a CDDA sector count as ``M:SS``."""
    total = max(0, round(sectors / 75.0))
    minutes, seconds = divmod(total, 60)
    return f'{minutes}:{seconds:02d}'


@dataclass(frozen=True)
class TrackInfo:
    """One audio track from the disc TOC."""

    number: int
    length_sectors: int
    start_sector: int = 0
    # INDEX 00 length ending at *start_sector* (0 until pregaps are detected).
    pregap_sectors: int = 0

    @property
    def duration_seconds(self) -> float:
        # CDDA: 75 sectors per second
        return self.length_sectors / 75.0

    @property
    def duration_label(self) -> str:
        return sectors_to_mmss(self.length_sectors)


@dataclass(frozen=True)
class DataTrackInfo:
    """One data track (mixed-mode track 1, or CD-Extra session 2)."""

    number: int
    start_sector: int
    length_sectors: int
    mode: int = 1  # 1 or 2
    session: int = 1
    pregap_sectors: int = 0

    @property
    def cue_mode(self) -> str:
        return 'MODE2/2352' if self.mode == 2 else 'MODE1/2352'

    @property
    def duration_label(self) -> str:
        return sectors_to_mmss(self.length_sectors)


@dataclass
class DiscInfo:
    """Summary of an inserted audio CD."""

    device: str
    tracks: list[TrackInfo] = field(default_factory=list)
    freedb_id: str | None = None
    data_tracks: list[DataTrackInfo] = field(default_factory=list)
    pregap_source: str = ''
    xa: bool = False
    leadout_lba: int = 0

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
    or no audio tracks are found. Data tracks (ioctl TOC) are attached when
    the kernel exposes them; INDEX 00 pregaps are filled later at rip start.
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
        # Keep TOC probe bounded. Always run off the GTK thread — cdparanoia can
        # block for several seconds while the drive spins up.
        completed = subprocess.run(
            [cdparanoia, '-Q', '-d', device],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
            # Start a new session so a timed-out probe does not leave orphans
            # holding the device open.
            start_new_session=True,
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

    info = DiscInfo(
        device=device,
        tracks=tracks,
        freedb_id=freedb,
    )
    attach_mmc_toc(info)
    return info


def attach_mmc_toc(disc: DiscInfo) -> None:
    """Fill data tracks and track-1 TOC pregap from CD-ROM ioctl TOC."""
    from ready2rip.disc.mmc import read_raw_toc

    toc = read_raw_toc(disc.device)
    if toc is None:
        _apply_track1_toc_pregap(disc)
        return

    disc.leadout_lba = toc.leadout_lba
    disc.xa = toc.xa
    starts = [t.start_lba for t in toc.tracks] + [toc.leadout_lba]
    session2 = toc.last_session_lba
    data: list[DataTrackInfo] = []
    for i, raw in enumerate(toc.tracks):
        length = starts[i + 1] - raw.start_lba
        if length <= 0:
            continue
        session = 1
        if session2 is not None and raw.start_lba >= session2:
            session = 2
        if not raw.is_data:
            continue
        mode = raw.datamode if raw.datamode in (1, 2) else 1
        data.append(
            DataTrackInfo(
                number=raw.number,
                start_sector=raw.start_lba,
                length_sectors=length,
                mode=mode,
                session=session,
            )
        )
    disc.data_tracks = data
    _apply_track1_toc_pregap(disc)
    if data:
        log.info(
            'MMC TOC: %d audio, %d data track(s) on %s',
            len(disc.tracks),
            len(data),
            disc.device,
        )


def _apply_track1_toc_pregap(disc: DiscInfo) -> None:
    if not disc.tracks:
        return
    t0 = disc.tracks[0]
    if t0.number != 1:
        return
    pregap = max(0, int(t0.start_sector))
    if t0.pregap_sectors == pregap:
        return
    disc.tracks[0] = replace(t0, pregap_sectors=pregap)


def _parse_tracks(output: str) -> list[TrackInfo]:
    tracks: list[TrackInfo] = []
    for match in _TRACK_LINE_RE.finditer(output):
        number = int(match.group(1))
        length = int(match.group(2))
        start = int(match.group(6))
        pregap = start if number == 1 else 0
        tracks.append(
            TrackInfo(
                number=number,
                length_sectors=length,
                start_sector=start,
                pregap_sectors=pregap,
            )
        )
    return tracks
