# SPDX-License-Identifier: GPL-3.0-or-later
"""CTDB submit quality gate (gi-free; safe for unit tests)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ready2rip.rip.paranoia_stats import ParanoiaStats


def ctdb_submit_quality(
    *,
    test_and_copy: bool,
    burst_tracks: Sequence[int],
    test_copy_mismatches: Sequence[int],
    track_entries: Sequence[Any] | None = None,
    image_stats: ParanoiaStats | None = None,
    image_sectors: int = 0,
) -> int:
    """Return 100 only for clean secure test&copy with perfect paranoia quality.

    *track_entries* are objects with ``number``, ``quality_percent``,
    ``had_errors``, and ``extract_mode`` (e.g. rip-log track rows).
    """
    if not test_and_copy or burst_tracks or test_copy_mismatches:
        return 0
    if image_stats is not None:
        if image_stats.had_errors:
            return 0
        q = image_stats.quality_percent(image_sectors)
        if q is None or q < 100.0:
            return 0
    if track_entries is not None:
        audio = [t for t in track_entries if int(t.number) > 0]
        if not audio:
            return 0
        for entry in audio:
            if entry.had_errors:
                return 0
            mode = (entry.extract_mode or '').lower()
            if 'burst' in mode:
                return 0
            q = entry.quality_percent
            if q is None or float(q) < 100.0:
                return 0
    return 100
