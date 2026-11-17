# SPDX-License-Identifier: GPL-3.0-or-later
"""Rip log outcome footer and require-match settings."""

from __future__ import annotations

from ready2rip.rip.riplog import RipLog


def test_riplog_outcome_footer() -> None:
    log = RipLog()
    log.verify_accuraterip = True
    log.require_db_match = True
    log.set_outcome(
        secure_ok=True,
        db_verified=False,
        archival_ok=True,
        summary='Secure OK · not in AccurateRip/CTDB',
    )
    log.finish()
    text = log.render()
    assert 'Require AccurateRip match' in text
    assert '≥1 track MATCH' in text
    assert 'Outcome: Archival OK · Secure OK · not DB-verified' in text


def test_riplog_outcome_fail() -> None:
    log = RipLog()
    log.set_outcome(
        secure_ok=False,
        db_verified=False,
        archival_ok=False,
        summary='Archival fail',
    )
    log.finish(error='AccurateRip error on track(s) 1')
    text = log.render()
    assert 'Outcome: Archival FAIL' in text
    assert 'not secure (burst)' in text
    # Outcome precedes the Errors line (no “No errors” above FAIL).
    assert text.index('Outcome: Archival FAIL') < text.index('Errors occurred:')
    assert 'No errors occurred' not in text
