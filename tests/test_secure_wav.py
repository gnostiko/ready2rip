# SPDX-License-Identifier: GPL-3.0-or-later
"""WAV size gate + overread helpers (no optical hardware / no PyGObject)."""

from __future__ import annotations

from types import SimpleNamespace

from ready2rip.disc.overread import (
    OverreadCapability,
    offset_margin_sectors,
    overread_span,
)
from ready2rip.rip.ctdb_quality import ctdb_submit_quality
from ready2rip.rip.paranoia_stats import ParanoiaStats
from ready2rip.rip.secure import (
    SECURE_MIN_WAV_RATIO,
    SECURE_WAV_SHORT_BYTES,
    wav_size_minimum,
)


def test_wav_size_minimum_exact_with_one_sector_slack() -> None:
    assert SECURE_MIN_WAV_RATIO == 1.0
    expected = 44 + 2352 * 100
    assert wav_size_minimum(expected, 1.0) == expected - SECURE_WAV_SHORT_BYTES
    assert wav_size_minimum(expected, 0.99) == int(expected * 0.99)


def test_offset_margin_sectors() -> None:
    assert offset_margin_sectors(0) == 0
    assert offset_margin_sectors(6) == 1
    assert offset_margin_sectors(588) == 1
    assert offset_margin_sectors(589) == 2
    assert offset_margin_sectors(-1200) == 3


def test_overread_span_expands_when_capable() -> None:
    over = OverreadCapability(lead_in=True, lead_out=True)
    start, end = overread_span(
        0,
        1000,
        sample_offset=667,
        overread=over,
        at_disc_start=True,
        at_disc_end=True,
    )
    assert start < 0
    assert end > 1000

    none = OverreadCapability()
    start2, end2 = overread_span(
        0,
        1000,
        sample_offset=667,
        overread=none,
        at_disc_start=True,
        at_disc_end=True,
    )
    assert (start2, end2) == (0, 1000)


def test_ctdb_submit_quality_requires_clean_paranoia() -> None:
    entry = SimpleNamespace(
        number=1,
        quality_percent=100.0,
        had_errors=False,
        extract_mode='secure (test & copy)',
    )
    assert (
        ctdb_submit_quality(
            test_and_copy=True,
            burst_tracks=[],
            test_copy_mismatches=[],
            track_entries=[entry],
        )
        == 100
    )

    entry.quality_percent = 99.5
    assert (
        ctdb_submit_quality(
            test_and_copy=True,
            burst_tracks=[],
            test_copy_mismatches=[],
            track_entries=[entry],
        )
        == 0
    )

    stats = ParanoiaStats()
    stats.reads = 10
    stats.wrote = 10
    stats.finished = True
    assert (
        ctdb_submit_quality(
            test_and_copy=True,
            burst_tracks=[],
            test_copy_mismatches=[],
            track_entries=None,
            image_stats=stats,
            image_sectors=100,
        )
        == 100
    )
