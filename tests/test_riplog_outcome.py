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
    assert 'require AR match (≥1 MATCH)' in text
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
    assert 'not Secure OK' in text
    # Outcome precedes the Errors line (no “No errors” above FAIL).
    assert text.index('Outcome: Archival FAIL') < text.index('Errors occurred:')
    assert 'No errors occurred' not in text


def test_riplog_outcome_normal_not_labeled_burst() -> None:
    log = RipLog()
    log.rip_mode = 'normal'
    log.burst_mode = False
    log.set_outcome(
        secure_ok=False,
        db_verified=False,
        archival_ok=False,
        summary='Normal extract',
    )
    log.finish()
    text = log.render()
    assert 'Outcome: Archival FAIL · Normal ·' in text
    assert 'burst' not in text.lower().split('outcome:')[1].split('\n')[0]


def test_riplog_outcome_intentional_burst() -> None:
    from ready2rip.rip.riplog import TrackLogEntry

    log = RipLog()
    log.rip_mode = 'burst'
    log.burst_mode = True
    entry = TrackLogEntry(number=1)
    entry.extract_mode = 'burst (-Z)'
    entry.had_errors = False
    entry.status = 'OK'
    log.tracks.append(entry)
    log.set_outcome(
        secure_ok=False,
        db_verified=False,
        archival_ok=True,
        summary='Burst mode',
    )
    log.finish()
    text = log.render()
    assert 'Outcome: Archival OK · Burst mode ·' in text
    assert 'paranoia disabled' in text
    assert 'Secure paranoia failed' not in text
    assert 'There were errors on track(s)' not in text
    assert 'burst fallback' not in text.lower()
