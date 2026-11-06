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
