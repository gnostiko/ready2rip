# SPDX-License-Identifier: GPL-3.0-or-later
"""Archival fail reasons and outcome tiers for finished rips."""

from __future__ import annotations

from pathlib import Path

from ready2rip.accuraterip import AccurateRipConfidence, AccurateRipResult
from ready2rip.ctdb import CtdbConfidence, CtdbResult
from ready2rip.rip.engine import RipJob
from ready2rip.rip.outcome import (
    archival_db_warnings,
    archival_fail_reasons,
    compute_tiers,
)


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
    reasons = archival_fail_reasons(
        _job(),
        ar_results=[],
        ctdb_results=[],
        burst_tracks=[3],
    )
    assert any('burst' in r.lower() for r in reasons)


def test_archival_ok_on_intentional_burst_mode() -> None:
    reasons = archival_fail_reasons(
        _job(burst_mode=True, verify_accuraterip=False, verify_ctdb=False),
        ar_results=[],
        ctdb_results=[],
        burst_tracks=[1, 2, 3],
    )
    assert reasons == []


def test_archival_lenient_ok_on_ar_mismatch() -> None:
    """Default: partial AccurateRip miss does not fail archival."""
    reasons = archival_fail_reasons(
        _job(verify_ctdb=False),
        ar_results=[
            AccurateRipResult(1, AccurateRipConfidence.MATCH),
            AccurateRipResult(2, AccurateRipConfidence.MISMATCH),
        ],
        ctdb_results=[],
        burst_tracks=[],
    )
    assert reasons == []
    warn = archival_db_warnings(
        _job(verify_ctdb=False),
        ar_results=[
            AccurateRipResult(1, AccurateRipConfidence.MATCH),
            AccurateRipResult(2, AccurateRipConfidence.MISMATCH),
        ],
        ctdb_results=[],
    )
    assert any('AccurateRip mismatch' in w for w in warn)


def test_archival_strict_fail_on_ar_mismatch() -> None:
    reasons = archival_fail_reasons(
        _job(archival_strict=True, verify_ctdb=False),
        ar_results=[
            AccurateRipResult(1, AccurateRipConfidence.MATCH),
            AccurateRipResult(2, AccurateRipConfidence.MISMATCH),
        ],
        ctdb_results=[],
        burst_tracks=[],
    )
    assert any('AccurateRip mismatch' in r for r in reasons)


def test_archival_strict_fail_ar_mismatch_even_in_burst_mode() -> None:
    reasons = archival_fail_reasons(
        _job(archival_strict=True, burst_mode=True, verify_ctdb=False),
        ar_results=[AccurateRipResult(1, AccurateRipConfidence.MISMATCH)],
        ctdb_results=[],
        burst_tracks=[1],
    )
    assert any('AccurateRip mismatch' in r for r in reasons)


def test_archival_ok_when_not_in_db() -> None:
    reasons = archival_fail_reasons(
        _job(),
        ar_results=[AccurateRipResult(1, AccurateRipConfidence.NOT_IN_DB)],
        ctdb_results=[CtdbResult(1, CtdbConfidence.NOT_IN_DB)],
        burst_tracks=[],
    )
    assert reasons == []


def test_archival_fail_on_ar_error() -> None:
    reasons = archival_fail_reasons(
        _job(verify_ctdb=False),
        ar_results=[AccurateRipResult(1, AccurateRipConfidence.ERROR)],
        ctdb_results=[],
        burst_tracks=[],
    )
    assert any('AccurateRip error' in r for r in reasons)


def test_archival_fail_on_ctdb_error() -> None:
    reasons = archival_fail_reasons(
        _job(verify_accuraterip=False),
        ar_results=[],
        ctdb_results=[CtdbResult(2, CtdbConfidence.ERROR)],
        burst_tracks=[],
    )
    assert any('CTDB error' in r for r in reasons)


def test_archival_fail_require_db_match_when_zero_matches() -> None:
    reasons = archival_fail_reasons(
        _job(require_db_match=True, verify_ctdb=False),
        ar_results=[
            AccurateRipResult(1, AccurateRipConfidence.NOT_IN_DB),
            AccurateRipResult(2, AccurateRipConfidence.NOT_IN_DB),
        ],
        ctdb_results=[],
        burst_tracks=[],
    )
    assert any('require ≥1 MATCH' in r for r in reasons)


def test_archival_fail_require_db_match_when_no_results() -> None:
    reasons = archival_fail_reasons(
        _job(require_db_match=True),
        ar_results=[],
        ctdb_results=[],
        burst_tracks=[],
    )
    # verify_accuraterip is on but empty results still trip require_db_match.
    assert any('require ≥1 MATCH' in r for r in reasons)


def test_archival_ok_require_db_match_when_matched() -> None:
    reasons = archival_fail_reasons(
        _job(require_db_match=True, verify_ctdb=False),
        ar_results=[AccurateRipResult(1, AccurateRipConfidence.MATCH)],
        ctdb_results=[],
        burst_tracks=[],
    )
    assert reasons == []


def test_archival_fail_skipped_unverified() -> None:
    reasons = archival_fail_reasons(
        _job(),
        ar_results=[],
        ctdb_results=[],
        burst_tracks=[],
        skipped_unverified=[1, 3],
    )
    assert any('Skipped existing' in r for r in reasons)


def test_archival_skips_ar_when_verify_off() -> None:
    reasons = archival_fail_reasons(
        _job(verify_accuraterip=False, verify_ctdb=False),
        ar_results=[AccurateRipResult(1, AccurateRipConfidence.MISMATCH)],
        ctdb_results=[CtdbResult(1, CtdbConfidence.MISMATCH)],
        burst_tracks=[],
    )
    assert reasons == []


def test_archival_lenient_ok_on_ctdb_mismatch_without_ar_match() -> None:
    reasons = archival_fail_reasons(
        _job(),
        ar_results=[AccurateRipResult(1, AccurateRipConfidence.NOT_IN_DB)],
        ctdb_results=[CtdbResult(1, CtdbConfidence.MISMATCH)],
        burst_tracks=[],
    )
    assert reasons == []
    warn = archival_db_warnings(
        _job(),
        ar_results=[AccurateRipResult(1, AccurateRipConfidence.NOT_IN_DB)],
        ctdb_results=[CtdbResult(1, CtdbConfidence.MISMATCH)],
    )
    assert any('CTDB mismatch' in w for w in warn)


def test_archival_strict_fail_on_ctdb_mismatch_without_ar_match() -> None:
    reasons = archival_fail_reasons(
        _job(archival_strict=True),
        ar_results=[AccurateRipResult(1, AccurateRipConfidence.NOT_IN_DB)],
        ctdb_results=[CtdbResult(1, CtdbConfidence.MISMATCH)],
        burst_tracks=[],
    )
    assert any('CTDB mismatch' in r for r in reasons)


def test_archival_ok_ctdb_mismatch_when_ar_matched() -> None:
    """AccurateRip match outweighs a CTDB exact-TOC conflict (pressing noise)."""
    reasons = archival_fail_reasons(
        _job(archival_strict=True),
        ar_results=[AccurateRipResult(1, AccurateRipConfidence.MATCH)],
        ctdb_results=[CtdbResult(1, CtdbConfidence.MISMATCH)],
        burst_tracks=[],
    )
    assert reasons == []


def test_archival_ok_on_ctdb_soft_unverified() -> None:
    reasons = archival_fail_reasons(
        _job(verify_accuraterip=False),
        ar_results=[],
        ctdb_results=[
            CtdbResult(
                1,
                CtdbConfidence.NOT_IN_DB,
                message='CTDB: not verified (no matching entry for this TOC)',
            )
        ],
        burst_tracks=[],
    )
    assert reasons == []


def test_archival_fail_when_ar_verify_produces_no_results() -> None:
    reasons = archival_fail_reasons(
        _job(verify_ctdb=False),
        ar_results=[],
        ctdb_results=[],
        burst_tracks=[],
    )
    assert any('no results' in r.lower() for r in reasons)


def test_archival_fail_ar_lookup_error_without_results() -> None:
    reasons = archival_fail_reasons(
        _job(verify_ctdb=False),
        ar_results=[],
        ctdb_results=[],
        burst_tracks=[],
        ar_lookup_error='timed out',
    )
    assert any('AccurateRip lookup failed' in r for r in reasons)


def test_tiers_secure_ok_partial_ar_match() -> None:
    ar = [
        AccurateRipResult(1, AccurateRipConfidence.MATCH),
        AccurateRipResult(2, AccurateRipConfidence.MISMATCH),
    ]
    ctdb = [
        CtdbResult(1, CtdbConfidence.MATCH),
        CtdbResult(2, CtdbConfidence.MISMATCH),
    ]
    reasons = archival_fail_reasons(
        _job(), ar_results=ar, ctdb_results=ctdb, burst_tracks=[]
    )
    tiers = compute_tiers(
        _job(),
        ar_results=ar,
        ctdb_results=ctdb,
        burst_tracks=[],
        fail_reasons=reasons,
    )
    assert reasons == []
    assert tiers.secure_ok
    assert not tiers.db_verified
    assert tiers.archival_ok
    assert 'Secure OK' in tiers.summary
    assert 'AR 1/2' in tiers.summary


def test_tiers_secure_ok_not_db_verified() -> None:
    ar = [AccurateRipResult(1, AccurateRipConfidence.NOT_IN_DB)]
    ctdb = [CtdbResult(1, CtdbConfidence.NOT_IN_DB)]
    reasons = archival_fail_reasons(
        _job(), ar_results=ar, ctdb_results=ctdb, burst_tracks=[]
    )
    tiers = compute_tiers(
        _job(),
        ar_results=ar,
        ctdb_results=ctdb,
        burst_tracks=[],
        fail_reasons=reasons,
    )
    assert tiers.secure_ok
    assert not tiers.db_verified
    assert tiers.archival_ok
    assert 'Secure OK' in tiers.summary
    assert 'not in AccurateRip/CTDB' in tiers.summary


def test_tiers_db_verified() -> None:
    ar = [AccurateRipResult(1, AccurateRipConfidence.MATCH)]
    ctdb = [CtdbResult(1, CtdbConfidence.MATCH)]
    reasons = archival_fail_reasons(
        _job(), ar_results=ar, ctdb_results=ctdb, burst_tracks=[]
    )
    tiers = compute_tiers(
        _job(),
        ar_results=ar,
        ctdb_results=ctdb,
        burst_tracks=[],
        fail_reasons=reasons,
    )
    assert tiers.secure_ok
    assert tiers.db_verified
    assert tiers.archival_ok
    assert 'DB verified' in tiers.summary


def test_tiers_burst_mode_not_secure() -> None:
    tiers = compute_tiers(
        _job(burst_mode=True),
        ar_results=[],
        ctdb_results=[],
        burst_tracks=[1],
        fail_reasons=[],
    )
    assert not tiers.secure_ok
    assert tiers.archival_ok
    assert 'Burst mode' in tiers.summary
