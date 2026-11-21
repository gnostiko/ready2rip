# SPDX-License-Identifier: GPL-3.0-or-later
"""Archival outcome tiers, fail reasons, and shared rip finish helpers."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ready2rip.accuraterip import AccurateRipConfidence, AccurateRipResult
from ready2rip.ctdb import CtdbConfidence, CtdbResult

if TYPE_CHECKING:
    from ready2rip.rip.engine_types import RipJob, RipResult
    from ready2rip.rip.riplog import RipLog

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class OutcomeTiers:
    """Three distinct “OK” signals for a finished rip."""

    secure_ok: bool
    db_verified: bool
    archival_ok: bool
    summary: str = ''


def _tracks_csv(nums: list[int]) -> str:
    return ', '.join(str(n) for n in nums)


def _ar_mismatch_tracks(ar_results: list[AccurateRipResult]) -> list[int]:
    return [
        r.track_number
        for r in ar_results
        if r.confidence == AccurateRipConfidence.MISMATCH
    ]


def _ar_error_tracks(ar_results: list[AccurateRipResult]) -> list[int]:
    return [
        r.track_number
        for r in ar_results
        if r.confidence == AccurateRipConfidence.ERROR
    ]


def _ctdb_mismatch_tracks(
    *,
    ar_results: list[AccurateRipResult],
    ctdb_results: list[CtdbResult],
) -> list[int]:
    """Exact-TOC CTDB conflicts, deferred when AccurateRip already matched."""
    ar_matched = {
        r.track_number
        for r in ar_results
        if r.confidence == AccurateRipConfidence.MATCH
    }
    return [
        r.track_number
        for r in ctdb_results
        if r.confidence == CtdbConfidence.MISMATCH
        and r.track_number not in ar_matched
    ]


def _ctdb_error_tracks(ctdb_results: list[CtdbResult]) -> list[int]:
    return [
        r.track_number
        for r in ctdb_results
        if r.confidence == CtdbConfidence.ERROR
    ]


def archival_db_warnings(
    job: RipJob,
    *,
    ar_results: list[AccurateRipResult],
    ctdb_results: list[CtdbResult],
) -> list[str]:
    """Pressing/DB mismatch notes (soft unless ``archival_strict``)."""
    warnings: list[str] = []
    if job.verify_accuraterip and ar_results:
        bad = _ar_mismatch_tracks(ar_results)
        if bad:
            warnings.append(
                f'AccurateRip mismatch on track(s) {_tracks_csv(bad)}'
            )
    if job.verify_ctdb and ctdb_results:
        bad = _ctdb_mismatch_tracks(
            ar_results=ar_results, ctdb_results=ctdb_results
        )
        if bad:
            warnings.append(f'CTDB mismatch on track(s) {_tracks_csv(bad)}')
    return warnings


def archival_fail_reasons(
    job: RipJob,
    *,
    ar_results: list[AccurateRipResult],
    ctdb_results: list[CtdbResult],
    burst_tracks: list[int],
    skipped_unverified: list[int] | None = None,
    ar_lookup_error: str | None = None,
    ctdb_lookup_error: str | None = None,
) -> list[str]:
    """Reasons a finished rip is not archival-grade.

    Default (lenient): hard-fail on insecure extract / verify infrastructure
    errors only. AccurateRip/CTDB *mismatches* are soft unless
    ``job.archival_strict`` is on. ``require_db_match`` still requires ≥1 MATCH.
    """
    reasons: list[str] = []
    # Intentional Burst mode already opted out of secure extract — do not
    # fail solely because every track used -Z. Fallback burst still fails.
    if burst_tracks and not job.burst_mode:
        reasons.append(
            f'Insecure burst (-Z) extraction on track(s) '
            f'{_tracks_csv(burst_tracks)}'
        )
    if skipped_unverified:
        reasons.append(
            f'Skipped existing track(s) {_tracks_csv(list(skipped_unverified))} '
            'without re-verification'
        )
    expect_verify = bool(job.track_numbers) or bool(job.copy_image)
    if job.verify_accuraterip:
        if ar_lookup_error and not ar_results:
            reasons.append(f'AccurateRip lookup failed: {ar_lookup_error}')
        if ar_results:
            errors = _ar_error_tracks(ar_results)
            if errors:
                reasons.append(
                    f'AccurateRip error on track(s) {_tracks_csv(errors)}'
                )
        elif expect_verify:
            reasons.append('AccurateRip: verification produced no results')
        if job.require_db_match:
            matches = [
                r
                for r in ar_results
                if r.confidence == AccurateRipConfidence.MATCH
            ]
            if not ar_results:
                reasons.append(
                    'AccurateRip: no verify results (require ≥1 MATCH is on)'
                )
            elif not matches:
                reasons.append(
                    'AccurateRip: no tracks matched (require ≥1 MATCH is on)'
                )
    if job.verify_ctdb:
        if ctdb_lookup_error and not ctdb_results:
            reasons.append(f'CTDB lookup failed: {ctdb_lookup_error}')
        if ctdb_results:
            errors = _ctdb_error_tracks(ctdb_results)
            if errors:
                reasons.append(f'CTDB error on track(s) {_tracks_csv(errors)}')
        elif expect_verify:
            reasons.append('CTDB: verification produced no results')
    if job.archival_strict:
        reasons.extend(
            archival_db_warnings(
                job, ar_results=ar_results, ctdb_results=ctdb_results
            )
        )
    return reasons


def summarize_ar(results: list[AccurateRipResult]) -> str:
    if not results:
        return ''
    matches = sum(1 for r in results if r.confidence == AccurateRipConfidence.MATCH)
    mismatches = sum(
        1 for r in results if r.confidence == AccurateRipConfidence.MISMATCH
    )
    missing = sum(1 for r in results if r.confidence == AccurateRipConfidence.NOT_IN_DB)
    errors = sum(1 for r in results if r.confidence == AccurateRipConfidence.ERROR)
    parts = [f'AR {matches}/{len(results)} match']
    if mismatches:
        parts.append(f'{mismatches} mismatch')
    if missing:
        parts.append(f'{missing} not in DB')
    if errors:
        parts.append(f'{errors} error')
    return ', '.join(parts)


def summarize_ctdb(results: list[CtdbResult]) -> str:
    if not results:
        return ''
    matches = sum(1 for r in results if r.confidence == CtdbConfidence.MATCH)
    mismatches = sum(1 for r in results if r.confidence == CtdbConfidence.MISMATCH)
    missing = sum(1 for r in results if r.confidence == CtdbConfidence.NOT_IN_DB)
    errors = sum(1 for r in results if r.confidence == CtdbConfidence.ERROR)
    if matches == len(results):
        return f'CTDB {matches}/{len(results)} match'
    parts = [f'CTDB {matches}/{len(results)} match']
    if mismatches:
        parts.append(f'{mismatches} mismatch')
    if missing:
        parts.append(f'{missing} unverified')
    if errors:
        parts.append(f'{errors} error')
    return ', '.join(parts)


def compute_tiers(
    job: RipJob,
    *,
    ar_results: list[AccurateRipResult],
    ctdb_results: list[CtdbResult],
    burst_tracks: list[int],
    fail_reasons: list[str],
) -> OutcomeTiers:
    """Derive secure / DB / archival tiers and a short summary label."""
    from ready2rip.settings_defs import normalize_rip_mode

    rip_mode = normalize_rip_mode(job.rip_mode)
    # Secure OK only for archival dual-pass without burst recovery.
    secure_ok = (
        rip_mode == 'archival'
        and not bool(burst_tracks)
        and not bool(job.burst_mode)
    )
    db_verified = _db_verified(job, ar_results=ar_results, ctdb_results=ctdb_results)
    archival_ok = not fail_reasons
    summary = _tier_summary(
        job,
        secure_ok=secure_ok,
        db_verified=db_verified,
        archival_ok=archival_ok,
        ar_results=ar_results,
        ctdb_results=ctdb_results,
        burst_tracks=burst_tracks,
    )
    return OutcomeTiers(
        secure_ok=secure_ok,
        db_verified=db_verified,
        archival_ok=archival_ok,
        summary=summary,
    )


def _db_verified(
    job: RipJob,
    *,
    ar_results: list[AccurateRipResult],
    ctdb_results: list[CtdbResult],
) -> bool:
    """True when every enabled DB check returned MATCH for all checked tracks."""
    checked = False
    if job.verify_accuraterip and ar_results:
        checked = True
        if any(r.confidence != AccurateRipConfidence.MATCH for r in ar_results):
            return False
    if job.verify_ctdb and ctdb_results:
        checked = True
        if any(r.confidence != CtdbConfidence.MATCH for r in ctdb_results):
            return False
    return checked


def _all_not_in_db(
    *,
    ar_results: list[AccurateRipResult],
    ctdb_results: list[CtdbResult],
    verify_ar: bool,
    verify_ctdb: bool,
) -> bool:
    """True when every checked result is NOT_IN_DB (no match / mismatch / error)."""
    has_any = False
    if verify_ar and ar_results:
        has_any = True
        if any(r.confidence != AccurateRipConfidence.NOT_IN_DB for r in ar_results):
            return False
    if verify_ctdb and ctdb_results:
        has_any = True
        if any(r.confidence != CtdbConfidence.NOT_IN_DB for r in ctdb_results):
            return False
    return has_any


def _tier_summary(
    job: RipJob,
    *,
    secure_ok: bool,
    db_verified: bool,
    archival_ok: bool,
    ar_results: list[AccurateRipResult],
    ctdb_results: list[CtdbResult],
    burst_tracks: list[int],
) -> str:
    if not archival_ok:
        return 'Archival fail'
    parts: list[str] = []
    from ready2rip.settings_defs import normalize_rip_mode

    rip_mode = normalize_rip_mode(job.rip_mode)
    if secure_ok:
        parts.append('Secure OK')
    elif job.burst_mode or rip_mode == 'burst':
        parts.append('Burst mode')
    elif rip_mode == 'normal':
        parts.append('Normal')
    elif burst_tracks:
        parts.append('Insecure burst')
    if db_verified:
        parts.append('DB verified')
    elif _all_not_in_db(
        ar_results=ar_results,
        ctdb_results=ctdb_results,
        verify_ar=bool(job.verify_accuraterip),
        verify_ctdb=bool(job.verify_ctdb),
    ):
        parts.append('not in AccurateRip/CTDB')
    else:
        ar = summarize_ar(ar_results) if job.verify_accuraterip else ''
        ctdb = summarize_ctdb(ctdb_results) if job.verify_ctdb else ''
        if ar:
            parts.append(ar)
        if ctdb:
            parts.append(ctdb)
    return ' · '.join(parts) if parts else 'Done'


def format_done_message(
    *,
    job: RipJob,
    outputs: list[Path],
    tiers: OutcomeTiers,
    ar_results: list[AccurateRipResult],
    ctdb_results: list[CtdbResult],
    burst_tracks: list[int],
    htoa_ripped: bool = False,
    data_count: int = 0,
    test_copy_mismatches: list[int] | None = None,
    copy_image: bool = False,
    image_label: str = '',
) -> str:
    """Banner/progress Done string shared by per-track and Copy Image paths."""
    if copy_image:
        label = image_label or 'image'
        if tiers.archival_ok:
            done_msg = f'Copy Image complete → {label}'
        else:
            done_msg = f'Copy Image failed archival checks → {label}'
    elif tiers.archival_ok:
        done_msg = f'Done — {len(outputs)} file(s)'
    else:
        done_msg = f'Rip failed archival checks — {len(outputs)} file(s) kept'
    if htoa_ripped:
        done_msg = f'{done_msg} (incl. HTOA)'
    if data_count:
        done_msg = f'{done_msg} · {data_count} data'
    ar_summary = summarize_ar(ar_results)
    ctdb_summary = summarize_ctdb(ctdb_results)
    if ar_summary:
        done_msg = f'{done_msg} · {ar_summary}'
    if ctdb_summary:
        done_msg = f'{done_msg} · {ctdb_summary}'
    if burst_tracks:
        if copy_image and burst_tracks == [0]:
            done_msg = f'{done_msg} · burst (-Z) on disc image'
        else:
            tracks_txt = ', '.join(str(n) for n in burst_tracks)
            done_msg = f'{done_msg} · burst (-Z) on track(s) {tracks_txt}'
    mismatches = test_copy_mismatches or []
    if job.test_and_copy and not job.burst_mode:
        if mismatches:
            tracks_txt = ', '.join(str(n) for n in mismatches)
            done_msg = (
                f'{done_msg} · test & copy mismatch on track(s) {tracks_txt} '
                '(burst recovery)'
            )
        else:
            done_msg = f'{done_msg} · test & copy OK'
    # Prefix Secure OK when local CRC passed but DB did not confirm.
    if (
        tiers.archival_ok
        and tiers.secure_ok
        and not tiers.db_verified
        and (ar_results or ctdb_results)
    ):
        if 'Secure OK' not in done_msg:
            done_msg = f'Secure OK · {done_msg}'
    return done_msg


def finish_result(
    *,
    success: bool,
    outputs: list[Path],
    album_dir: Path | None,
    ar_results: list[AccurateRipResult],
    notes: list[str],
    burst_tracks: list[int],
    rip_log: RipLog | None,
    error: str | None = None,
    cancelled: bool = False,
    cover_path: Path | None = None,
    htoa_ripped: bool = False,
    test_copy_mismatches: list[int] | None = None,
    log_dir: Path | None = None,
    ctdb_results: list[CtdbResult] | None = None,
    secure_ok: bool = True,
    db_verified: bool = False,
    archival_ok: bool | None = None,
    outcome_summary: str = '',
    skipped_existing: list[int] | None = None,
) -> RipResult:
    """Build a RipResult; write the rip log beside the *first* encode only.

    *log_dir* / *album_dir* should be the primary encode album folder. Falls
    back to the first output's parent when needed.
    """
    from ready2rip.rip.engine_types import RipResult

    log_path: Path | None = None
    ok_archival = success if archival_ok is None else archival_ok
    if rip_log is not None:
        rip_log.notes.extend(n for n in notes if n not in rip_log.notes)
        rip_log.set_outcome(
            secure_ok=secure_ok,
            db_verified=db_verified,
            archival_ok=ok_archival,
            summary=outcome_summary,
        )
        rip_log.finish(error=error, cancelled=cancelled)
        dest = log_dir or album_dir
        if dest is None and outputs:
            dest = outputs[0].parent
        if dest is not None:
            try:
                dest.mkdir(parents=True, exist_ok=True)
                log_path = rip_log.write(dest)
                notes.append(f'Rip log written to {log_path}')
            except OSError as exc:
                log.warning('Could not write rip log: %s', exc)
                notes.append(f'Rip log not written: {exc}')
    return RipResult(
        success=success,
        output_files=outputs,
        album_dir=album_dir,
        error=error,
        cancelled=cancelled,
        accuraterip=ar_results,
        ctdb=ctdb_results or [],
        notes=notes,
        burst_tracks=burst_tracks,
        log_path=log_path,
        cover_path=cover_path,
        htoa_ripped=htoa_ripped,
        test_copy_mismatches=test_copy_mismatches or [],
        secure_ok=secure_ok,
        db_verified=db_verified,
        archival_ok=ok_archival,
        outcome_summary=outcome_summary,
        skipped_existing=skipped_existing or [],
    )


def apply_burst_notes(
    job: RipJob,
    *,
    burst_tracks: list[int],
    notes: list[str],
) -> None:
    """Append human-readable burst notes (idempotent-ish via membership)."""
    if not burst_tracks:
        return
    # Copy Image uses track number 0 as a sentinel for the whole disc image.
    if job.copy_image and burst_tracks == [0]:
        subject = 'disc image'
    else:
        subject = f'track(s) {_tracks_csv(burst_tracks)}'
    if job.burst_mode:
        msg = (
            f'Burst mode (-Z) on {subject} '
            '(paranoia disabled; not archival-grade)'
        )
    else:
        msg = (
            f'Insecure burst extraction on {subject} '
            '(not archival-grade; rip marked failed)'
        )
    if msg not in notes:
        notes.append(msg)


def append_archival_notes(
    notes: list[str],
    *,
    fail_reasons: list[str],
    soft_warnings: list[str],
) -> None:
    """Merge fail reasons and soft DB warnings into *notes* (idempotent)."""
    for reason in fail_reasons:
        if reason not in notes:
            notes.append(reason)
    hard = set(fail_reasons)
    for warn in soft_warnings:
        if warn in hard:
            continue
        note = f'{warn} (archival still OK)'
        if note not in notes and warn not in notes:
            notes.append(note)


def evaluate_archival(
    job: RipJob,
    *,
    ar_results: list[AccurateRipResult],
    ctdb_results: list[CtdbResult],
    burst_tracks: list[int],
    notes: list[str],
    skipped_unverified: list[int] | None = None,
    ar_lookup_error: str | None = None,
    ctdb_lookup_error: str | None = None,
) -> tuple[list[str], OutcomeTiers]:
    """Compute fail reasons + tiers and append notes (burst + archival)."""
    fail_reasons = archival_fail_reasons(
        job,
        ar_results=ar_results,
        ctdb_results=ctdb_results,
        burst_tracks=burst_tracks,
        skipped_unverified=skipped_unverified,
        ar_lookup_error=ar_lookup_error,
        ctdb_lookup_error=ctdb_lookup_error,
    )
    soft = archival_db_warnings(
        job, ar_results=ar_results, ctdb_results=ctdb_results
    )
    apply_burst_notes(job, burst_tracks=burst_tracks, notes=notes)
    append_archival_notes(
        notes, fail_reasons=fail_reasons, soft_warnings=soft
    )
    tiers = compute_tiers(
        job,
        ar_results=ar_results,
        ctdb_results=ctdb_results,
        burst_tracks=burst_tracks,
        fail_reasons=fail_reasons,
    )
    return fail_reasons, tiers
