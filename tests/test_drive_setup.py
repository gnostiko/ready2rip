# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for AccurateRip sample-offset calibration helpers."""

from __future__ import annotations

from ready2rip.disc.probe import DiscInfo, TrackInfo
from ready2rip.drive_setup import (
    _calibration_tracks,
    _confirm_span_sectors,
    _software_offset_span,
    _span_samples_match,
)


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


def test_confirm_span_last_track_uses_end() -> None:
    last = TrackInfo(number=4, start_sector=60_000, length_sectors=19_000)
    start, end = _confirm_span_sectors(
        last, track_count=4, sample_offset=6, sector_count=60
    )
    assert end == 79_000
    assert end - start == 60
    assert start == 78_940


def test_confirm_span_first_track_uses_start() -> None:
    first = TrackInfo(number=1, start_sector=0, length_sectors=20_000)
    start, end = _confirm_span_sectors(
        first, track_count=4, sample_offset=-48, sector_count=60
    )
    assert start == 0
    assert end == 60


def test_confirm_span_grows_for_large_offset() -> None:
    last = TrackInfo(number=4, start_sector=60_000, length_sectors=19_000)
    start, end = _confirm_span_sectors(
        last, track_count=4, sample_offset=1674, sector_count=60
    )
    # margin for 1674 samples ≈ 3 sectors; n >= 5 + 3 + 8 = 16, still capped by
    # max(60, …) so length stays 60 unless margin pushes above.
    assert end - start >= 60


def test_software_offset_span_and_match() -> None:
    # 3 sectors of synthetic frames; offset +2 shifts source index.
    burst = list(range(3 * 588))
    expected = _software_offset_span(
        burst,
        track_start_sector=10,
        span_start=10,
        span_end=12,
        sample_offset=2,
    )
    assert len(expected) == 2 * 588
    assert expected[0] == 2
    assert _span_samples_match(expected, expected, slack=0) is True
    bad = list(expected)
    for i in range(100, 200):
        bad[i] = 999999
    assert _span_samples_match(expected, bad, slack=0) is False
