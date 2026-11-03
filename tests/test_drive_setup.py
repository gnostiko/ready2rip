# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for AccurateRip sample-offset calibration helpers."""

from __future__ import annotations

from ready2rip.disc.probe import DiscInfo, TrackInfo
from ready2rip.drive_setup import _calibration_tracks


def test_calibration_tracks_prefers_last_then_first() -> None:
    """Positive offsets like +6 only match on the last track's AR skip window."""
    disc = DiscInfo(
        device='/dev/sr0',
        tracks=[
            TrackInfo(number=1, start_sector=0, length_sectors=20_000),
            TrackInfo(number=2, start_sector=20_000, length_sectors=18_000),
            TrackInfo(number=3, start_sector=38_000, length_sectors=22_000),
            TrackInfo(number=4, start_sector=60_000, length_sectors=19_000),
        ],
    )
    ordered = _calibration_tracks(disc)
    assert [t.number for t in ordered[:2]] == [4, 1]
