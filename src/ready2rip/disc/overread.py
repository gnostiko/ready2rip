# SPDX-License-Identifier: GPL-3.0-or-later
"""Detect whether an optical drive can overread into lead-in / lead-out.

cdparanoia ``-O`` sample-offset correction needs real samples beyond the
TOC edges. When the drive cannot overread, missing edge samples are
silence-filled — AccurateRip still verifies (first/last 5-sector skip),
but the PCM at the disc edges is not recovered.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)

# CDDA frames per sector (588 stereo samples × 4 bytes = 2352).
_SAMPLES_PER_SECTOR = 588


@dataclass(frozen=True)
class OverreadCapability:
    """Result of a lead-in / lead-out READ CD probe."""

    lead_in: bool = False
    lead_out: bool = False

    @property
    def any(self) -> bool:
        return self.lead_in or self.lead_out

    @property
    def both(self) -> bool:
        return self.lead_in and self.lead_out

    @property
    def log_label(self) -> str:
        if self.both:
            return 'Yes'
        if self.lead_in and not self.lead_out:
            return 'Lead-In only'
        if self.lead_out and not self.lead_in:
            return 'Lead-Out only'
        return 'No'


def offset_margin_sectors(sample_offset: int) -> int:
    """Sectors needed past a TOC edge to cover *sample_offset* samples."""
    off = abs(int(sample_offset))
    if off <= 0:
        return 0
    return (off + _SAMPLES_PER_SECTOR - 1) // _SAMPLES_PER_SECTOR


def overread_span(
    start_sector: int,
    end_sector: int,
    *,
    sample_offset: int,
    overread: OverreadCapability,
    at_disc_start: bool,
    at_disc_end: bool,
) -> tuple[int, int]:
    """Widen a TOC span into lead-in/out when offset correction needs it."""
    margin = offset_margin_sectors(sample_offset)
    if margin <= 0:
        return int(start_sector), int(end_sector)
    start = int(start_sector)
    end = int(end_sector)
    # Non-zero offset needs samples past TOC edges (lead-in / lead-out).
    if int(sample_offset) != 0:
        if at_disc_start and overread.lead_in:
            start -= margin
        if at_disc_end and overread.lead_out:
            end += margin
    if end <= start:
        return int(start_sector), int(end_sector)
    return start, end


def per_track_overread_read_span(
    *,
    track_no: int,
    track_start: int,
    track_length: int,
    first_track_no: int,
    last_track_no: int,
    sample_offset: int,
    overread: OverreadCapability,
) -> tuple[int, int, int] | None:
    """Return ``(read_start, read_end, toc_expected_bytes)`` for edge overread.

    Only the first/last audio tracks need lead-in/out samples for ``-O``.
    Middle tracks keep track-number extraction (neighbors supply offset audio).
    Returns ``None`` when span extract is unnecessary.
    """
    if int(sample_offset) == 0 or not overread.any:
        return None
    at_start = int(track_no) == int(first_track_no)
    at_end = int(track_no) == int(last_track_no)
    if not at_start and not at_end:
        return None
    toc_start = int(track_start)
    toc_end = toc_start + int(track_length)
    read_start, read_end = overread_span(
        toc_start,
        toc_end,
        sample_offset=sample_offset,
        overread=overread,
        at_disc_start=at_start,
        at_disc_end=at_end,
    )
    if (read_start, read_end) == (toc_start, toc_end):
        return None
    toc_expected = 44 + int(track_length) * 2352
    return read_start, read_end, toc_expected


def probe_overread(
    device: str,
    *,
    leadout_lba: int = 0,
) -> OverreadCapability:
    """Try MMC READ CD one sector before LBA 0 and at/after lead-out."""
    lead_in = False
    lead_out = False
    try:
        from ready2rip.disc.mmc import MmcDevice

        with MmcDevice(device) as mmc:
            # Lead-in: LBA -1 (0xFFFFFFFF as unsigned 32-bit).
            lead_in = mmc.read_raw_sectors(0xFFFFFFFF, 1) is not None
            if not lead_in:
                # Some firmwares accept signed-style -1 via high bit only.
                lead_in = mmc.read_raw_sectors(-1 & 0xFFFFFFFF, 1) is not None

            lo = int(leadout_lba)
            if lo > 0:
                # At lead-out LBA, then one sector past.
                lead_out = (
                    mmc.read_raw_sectors(lo, 1) is not None
                    or mmc.read_raw_sectors(lo + 1, 1) is not None
                )
    except Exception as exc:
        log.debug('Overread probe failed on %s: %s', device, exc)
    return OverreadCapability(lead_in=lead_in, lead_out=lead_out)
