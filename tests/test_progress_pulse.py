# SPDX-License-Identifier: GPL-3.0-or-later
"""ProgressPulse heartbeat helper (no PyGObject)."""

from __future__ import annotations

import time

from ready2rip.rip.progress_pulse import ProgressPulse, pulse_while


def test_progress_pulse_ticks() -> None:
    hits: list[float] = []

    def tick() -> None:
        hits.append(time.monotonic())

    with ProgressPulse(tick, interval=0.05):
        time.sleep(0.18)
    assert len(hits) >= 2


def test_pulse_while_returns_work_result() -> None:
    hits = 0

    def tick() -> None:
        nonlocal hits
        hits += 1

    result = pulse_while(tick, lambda: 42, interval=0.05)
    assert result == 42
    assert hits >= 1
