# SPDX-License-Identifier: GPL-3.0-or-later
"""Map per-unit (track) progress into one whole-rip fraction (gi-free)."""

from __future__ import annotations


def normalize_unit_weights(sector_counts: list[int] | list[float]) -> list[float]:
    """Positive weights for each work unit (track / HTOA / data)."""
    if not sector_counts:
        return [1.0]
    return [float(max(1, int(s))) for s in sector_counts]


def overall_fraction(index: float, sub: float, weights: list[float]) -> float:
    """Whole-rip 0..1 from unit *index* and within-unit *sub* (0..1)."""
    if not weights:
        return max(0.0, min(1.0, sub))
    total = sum(weights)
    if total <= 0:
        return max(0.0, min(1.0, sub))
    n = len(weights)
    if index >= n:
        return 1.0
    idx = int(max(0.0, index))
    if idx >= n:
        return 1.0
    done = sum(weights[:idx])
    done += max(0.0, min(1.0, sub)) * weights[idx]
    return max(0.0, min(1.0, done / total))


def unit_end_fraction(index: float, weights: list[float]) -> float:
    """Fraction at the end of the unit that contains *index*."""
    if not weights:
        return 1.0
    total = sum(weights)
    if total <= 0:
        return 1.0
    n = len(weights)
    if index >= n:
        return 1.0
    idx = int(max(0.0, index))
    if idx >= n:
        return 1.0
    return sum(weights[: idx + 1]) / total
