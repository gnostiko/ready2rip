# SPDX-License-Identifier: GPL-3.0-or-later
"""Archival fail-closed reasons for finished rips."""

from __future__ import annotations

from pathlib import Path

from ready2rip.accuraterip import AccurateRipConfidence, AccurateRipResult
from ready2rip.ctdb import CtdbConfidence, CtdbResult
from ready2rip.rip.engine import RipJob, _archival_fail_reasons


def _job(**kwargs) -> RipJob:
    base = dict(
        device='/dev/sr0',
        track_numbers=[1, 2],
        output_directory=Path('/tmp'),
        verify_accuraterip=True,
        verify_ctdb=True,
    )
    base.update(kwargs)
    return RipJob(**base)  # type: ignore[arg-type]


def test_archival_fail_on_burst() -> None:
    reasons = _archival_fail_reasons(
        _job(),
        ar_results=[],
        ctdb_results=[],
        burst_tracks=[3],
    )
    assert any('burst' in r.lower() for r in reasons)


def test_archival_ok_on_intentional_burst_mode() -> None:
    reasons = _archival_fail_reasons(
        _job(burst_mode=True),
        ar_results=[],
        ctdb_results=[],
        burst_tracks=[1, 2, 3],
    )
    assert reasons == []


def test_archival_fail_ar_mismatch_even_in_burst_mode() -> None:
    reasons = _archival_fail_reasons(
        _job(burst_mode=True),
        ar_results=[AccurateRipResult(1, AccurateRipConfidence.MISMATCH)],
        ctdb_results=[],
        burst_tracks=[1],
    )
    assert any('AccurateRip mismatch' in r for r in reasons)


def test_archival_fail_on_ar_mismatch() -> None:
    reasons = _archival_fail_reasons(
        _job(),
        ar_results=[
            AccurateRipResult(1, AccurateRipConfidence.MATCH),
            AccurateRipResult(2, AccurateRipConfidence.MISMATCH),
        ],
        ctdb_results=[],
        burst_tracks=[],
    )
    assert any('AccurateRip mismatch' in r for r in reasons)


def test_archival_ok_when_not_in_db() -> None:
    reasons = _archival_fail_reasons(
        _job(),
        ar_results=[AccurateRipResult(1, AccurateRipConfidence.NOT_IN_DB)],
        ctdb_results=[CtdbResult(1, CtdbConfidence.NOT_IN_DB)],
        burst_tracks=[],
    )
    assert reasons == []


def test_archival_skips_ar_when_verify_off() -> None:
    reasons = _archival_fail_reasons(
        _job(verify_accuraterip=False, verify_ctdb=False),
        ar_results=[AccurateRipResult(1, AccurateRipConfidence.MISMATCH)],
        ctdb_results=[CtdbResult(1, CtdbConfidence.MISMATCH)],
        burst_tracks=[],
    )
    assert reasons == []
