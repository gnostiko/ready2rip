# SPDX-License-Identifier: GPL-3.0-or-later
"""Whole-rip progress mapping (no PyGObject)."""

from __future__ import annotations

from ready2rip.rip.progress_map import (
    normalize_unit_weights,
    overall_fraction,
    unit_end_fraction,
)


def test_equal_weights_match_index_over_n() -> None:
    weights = normalize_unit_weights([1, 1, 1, 1])
    assert overall_fraction(0, 0.0, weights) == 0.0
    assert overall_fraction(0, 1.0, weights) == 0.25
    assert overall_fraction(1, 0.5, weights) == 0.375
    assert overall_fraction(3, 1.0, weights) == 1.0


def test_sector_weights_favor_longer_tracks() -> None:
    # Track 1 is 10× longer than track 2 — half of track 1 ≈ half the rip.
    weights = normalize_unit_weights([10_000, 1_000])
    mid_first = overall_fraction(0, 0.5, weights)
    assert 0.40 <= mid_first <= 0.50
    assert overall_fraction(0, 1.0, weights) == unit_end_fraction(0, weights)
    assert unit_end_fraction(0, weights) > 0.85
    assert abs(overall_fraction(1, 1.0, weights) - 1.0) < 1e-9


def test_unit_end_caps_nudge_inside_current_track() -> None:
    weights = normalize_unit_weights([100, 100, 100])
    # End of track 0 is 1/3 — nudge must not be allowed past that ceiling.
    assert abs(unit_end_fraction(0, weights) - (1.0 / 3.0)) < 1e-9
    assert abs(unit_end_fraction(1, weights) - (2.0 / 3.0)) < 1e-9


def test_mid_extract_moves_past_completed_units() -> None:
    weights = normalize_unit_weights([200, 200])
    start_second = overall_fraction(1, 0.0, weights)
    mid_second = overall_fraction(1, 0.5, weights)
    end_second = overall_fraction(1, 1.0, weights)
    assert abs(start_second - 0.5) < 1e-9
    assert 0.70 <= mid_second <= 0.80
    assert abs(end_second - 1.0) < 1e-9
    assert unit_end_fraction(0, weights) <= mid_second
