from __future__ import annotations

import logging
import subprocess
import threading
import time
from collections import defaultdict
from pathlib import Path

from ready2rip.accuraterip import (
    AccurateRipResult,
    AccurateRipVerifier,
)
from ready2rip.disc.htoa import (
    detect_htoa,
)
from ready2rip.replaygain import apply_replaygain
from ready2rip.rip.artwork_prep import prepare_artwork as _prepare_artwork
from ready2rip.rip.encode_dest import (
    is_lossless_extension,
    is_lossless_format,
)
from ready2rip.rip.engine_copy_image import CopyImageMixin
from ready2rip.rip.engine_encode import EncodeMixin
from ready2rip.rip.engine_extract import ExtractMixin
from ready2rip.rip.engine_helpers import _expected_wav_sizes
from ready2rip.rip.engine_paranoia import ParanoiaMixin
from ready2rip.rip.engine_types import (
    EmbedArtworkSet as EmbedArtworkSet,
)
from ready2rip.rip.engine_types import (
    ProgressCallback as ProgressCallback,
)
from ready2rip.rip.engine_types import (
    RipJob as RipJob,
)
from ready2rip.rip.engine_types import (
    RipProgress as RipProgress,
)
from ready2rip.rip.engine_types import (
    RipResult as RipResult,
)
from ready2rip.rip.engine_types import (
    RipState as RipState,
)
from ready2rip.rip.engine_types import (
    _CrcMismatch,
    _PendingEncode,
)
from ready2rip.rip.engine_verify import VerifyMixin
from ready2rip.rip.job_extract import sync_job_extract_flags
from ready2rip.rip.outcome import (
    evaluate_archival,
    finish_result,
    format_done_message,
)
from ready2rip.rip.plan import (
    ENCODE_EXTENSIONS,
    unique_album_dirs,
)
from ready2rip.rip.progress_map import (
    normalize_unit_weights,
    overall_fraction,
    unit_end_fraction,
)
from ready2rip.rip.progress_pulse import ProgressPulse
from ready2rip.rip.riplog import (
    RipLog,
)
from ready2rip.rip.tmpdir import rip_scratch_dir
from ready2rip.tags.writer import TagWriter
from ready2rip.util import find_cdparanoia, validate_device_path

log = logging.getLogger(__name__)


class RipEngine(
    VerifyMixin,
    CopyImageMixin,
    ExtractMixin,
    EncodeMixin,
    ParanoiaMixin,
):
    """Coordinates test&copy, HTOA, AccurateRip, encode, tag, ReplayGain, art."""

    EXTENSIONS = ENCODE_EXTENSIONS

    def __init__(self) -> None:
        self._cancelled = False
        self._proc_lock = threading.Lock()
        self._procs: set[subprocess.Popen] = set()
        self._encode_pending: _PendingEncode | None = None
        self._tags = TagWriter()

    def cancel(self) -> None:
        self._cancelled = True
        with self._proc_lock:
            procs = list(self._procs)
        for proc in procs:
            if proc.poll() is None:
                try:
                    proc.terminate()
                except OSError:
                    pass
        # Best-effort: drop in-flight encode outputs. finish_pending(abandon)
        # also cleans up after the worker exits.
        pending = self._encode_pending
        if pending is not None:
            for dest in pending.dests:
                try:
                    dest.out_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def run(self, job: RipJob, on_progress: ProgressCallback | None = None) -> RipResult:
        self._cancelled = False
        sync_job_extract_flags(job)
        tracks = sorted(n for n in job.track_numbers if n > 0)
        if not tracks and not job.rip_htoa and not job.copy_image:
            return RipResult(success=False, error='No tracks selected')

        if find_cdparanoia() is None:
            return RipResult(
                success=False,
                error='cdparanoia not found (install libcdio-paranoia / cd-paranoia)',
            )

        try:
            job.device = validate_device_path(job.device)
        except ValueError as exc:
            return RipResult(success=False, error=str(exc))

        fmt = (job.encode_format or 'flac').strip().lower()
        if fmt not in self.EXTENSIONS:
            fmt = 'flac'
        base = Path(job.output_directory).expanduser().resolve()

        # CD images + CUE need a lossless/container format.
        if job.copy_image and not is_lossless_format(fmt):
            log.info('Copy Image mode: using FLAC instead of %s', fmt)
            fmt = 'flac'

        missing = self._missing_encoder(fmt)
        if missing:
            return RipResult(success=False, error=missing)
        if job.also_encode and not job.copy_image:
            also_fmt = (job.also_encode_format or 'mp3').strip().lower()
            if also_fmt != fmt:
                also_missing = self._missing_encoder(also_fmt)
                if also_missing:
                    return RipResult(success=False, error=also_missing)

        job.encode_format = fmt
        job.output_directory = base

        # Probe lead-in/out on the worker thread (not the GTK main loop).
        self._probe_job_overread(job, on_progress)

        layout_notes = self._detect_disc_layout(job, on_progress)

        if job.copy_image:
            return self._run_copy_image(job, base, fmt, on_progress, layout_notes=layout_notes)

        # Work units: optional HTOA (track 0) + selected audio tracks.
        outputs: list[Path] = []
        ar_results: list[AccurateRipResult] = []
        notes: list[str] = list(layout_notes)
        burst_tracks: list[int] = []
        skipped_unverified: list[int] = []
        test_copy_mismatches: list[int] = []
        cover_path: Path | None = None
        htoa_ripped = False
        expected_sizes = _expected_wav_sizes(job.disc_info)

        rip_log: RipLog | None = None
        if job.write_rip_log:
            rip_log = RipLog()
            rip_log.configure_from_job(job)

        # --- All destination album folders + artwork before disc reads ---
        first_for_paths = tracks[0] if tracks else 1
        try:
            dests0 = self._destinations_for_track(job, first_for_paths)
            self._ensure_destination_dirs(dests0)
        except OSError as exc:
            return RipResult(
                success=False,
                error=f'Cannot create output folder(s): {exc}',
            )
        # Cover art at the album meta root; log beside the primary encode.
        meta_dir0 = dests0[0].meta_dir
        primary_audio_dir = dests0[0].album_dir
        album_dir = primary_audio_dir
        for dest in dests0:
            target_note = f'Encode target: {dest.format.upper()} → {dest.album_dir}'
            notes.append(target_note)
            if rip_log is not None:
                rip_log.notes.append(target_note)

        # Sector-weighted work units so long tracks move the banner more.
        htoa_info = detect_htoa(job.disc_info) if job.rip_htoa else None
        data_tracks = (
            list(job.disc_info.data_tracks)
            if job.rip_data_tracks and job.disc_info is not None
            else []
        )
        weight_sectors: list[int] = []
        if htoa_info is not None:
            weight_sectors.append(int(htoa_info.length_sectors))
        for track_no in tracks:
            toc_track = self._toc_track(job.disc_info, track_no)
            weight_sectors.append(int(toc_track.length_sectors) if toc_track is not None else 1)
        for data_track in data_tracks:
            weight_sectors.append(int(data_track.length_sectors))
        unit_weights = normalize_unit_weights(weight_sectors or [1])
        total_units = float(len(unit_weights)) or 1.0

        last_overall = 0.0
        progress_lock = threading.Lock()
        last_emit_at = 0.0
        last_emit_message = ''
        last_emit_fraction = -1.0

        def report(
            track_number: int,
            state: RipState,
            *,
            index: float = 0.0,
            sub: float = 0.0,
            message: str = '',
            total_units: float | None = None,
            nudge: bool = False,
        ) -> None:
            """Map within-unit *sub* into whole-rip fill; keep status live.

            *total_units* is accepted for call-site compatibility; weights from
            sector counts drive the fraction. *nudge* heartbeats ease the fill
            toward the current unit end during blocking CRC / verify / encode.
            """
            nonlocal last_overall, last_emit_at, last_emit_message, last_emit_fraction
            if on_progress is None:
                return
            _ = total_units  # call sites still pass this; weights are authoritative
            sub_c = 1.0 if state == RipState.DONE else max(0.0, min(1.0, sub))
            measured = overall_fraction(index, sub_c, unit_weights)
            ceiling = unit_end_fraction(index, unit_weights)
            with progress_lock:
                if nudge:
                    target = min(ceiling, max(measured, last_overall + 0.0025))
                    overall = max(last_overall, target)
                else:
                    overall = measured
                last_overall = max(last_overall, min(1.0, overall))
                fraction = last_overall
            # Drop duplicate heartbeats that would only spam the GTK idle queue.
            now = time.monotonic()
            if (
                nudge
                and message == last_emit_message
                and abs(fraction - last_emit_fraction) < 0.0015
                and (now - last_emit_at) < 0.12
            ):
                return
            last_emit_at = now
            last_emit_message = message
            last_emit_fraction = fraction
            on_progress(
                RipProgress(
                    track_number=track_number,
                    state=state,
                    fraction=fraction,
                    message=message,
                )
            )

        report(
            first_for_paths,
            RipState.PREPARING,
            index=0.0,
            sub=0.0,
            message='Preparing album folder and artwork…',
        )

        embed_art, _folder_art, cover_path, art_notes = _prepare_artwork(job, meta_dir0)
        notes.extend(art_notes)
        if cover_path is not None:
            notes.append(f'Cover art saved: {cover_path}')
            if rip_log is not None:
                rip_log.notes.append(f'Cover art saved before rip: {cover_path}')

        # AccurateRip setup. Sample offset is applied at extract time via
        # cdparanoia -O; both verifiers hash the offset-corrected WAV as-is.
        ar_verifier: AccurateRipVerifier | None = None
        if job.verify_accuraterip and job.disc_info is not None:
            ar_verifier = AccurateRipVerifier(sample_offset=0)
            status = ar_verifier.prepare(job.disc_info)
            notes.append(status)
            if job.sample_offset:
                notes.append(
                    f'Read offset {job.sample_offset} samples applied during extraction (-O)'
                )
            if not job.offset_configured:
                notes.append(
                    'WARNING: Drive sample offset was not calibrated — '
                    'AccurateRip results may be unreliable'
                )
            if rip_log is not None:
                rip_log.ar_status = status
            report(
                first_for_paths,
                RipState.VERIFYING,
                index=0.0,
                sub=0.02,
                message=status,
            )


        disc_track_count = job.disc_track_count or (
            job.disc_info.track_count if job.disc_info else (max(tracks) if tracks else 0)
        )
        if ar_verifier is not None:
            ar_verifier.set_total_tracks(disc_track_count)

        # Defeat drive audio cache between archival test and copy passes.
        defeat_cache = bool(job.defeat_audio_cache)
        if defeat_cache and job.test_and_copy and not job.burst_mode:
            notes.append('Cache defeat enabled between test and copy')

        unit_index = 0.0
        htoa_path: Path | None = None
        htoa_paths: list[Path] = []
        # (track_number, path) for multi-file CUE generation after encode.
        track_output_pairs: list[tuple[int, Path]] = []
        data_output_pairs: list[tuple[int, Path]] = []

        try:
            with rip_scratch_dir(job.output_directory) as tmp_path:

                # --- HTOA ---
                if htoa_info is not None and not self._cancelled:
                    report(
                        0,
                        RipState.RIPPING,
                        index=unit_index,
                        sub=0.0,
                        message=htoa_info.message,
                        total_units=total_units,
                    )
                    htoa_ok, htoa_path, htoa_paths, htoa_notes = self._rip_htoa_unit(
                        job=job,
                        htoa_info=htoa_info,
                        tmp_path=tmp_path,
                        embed_art=embed_art,
                        defeat_cache=defeat_cache,
                        rip_log=rip_log,
                        report=lambda state, sub, msg: report(
                            0,
                            state,
                            index=unit_index,
                            sub=sub,
                            message=msg,
                            total_units=total_units,
                        ),
                    )
                    notes.extend(htoa_notes)
                    if htoa_ok and htoa_paths:
                        outputs.extend(htoa_paths)
                        htoa_ripped = True
                        if htoa_path is None:
                            htoa_path = htoa_paths[0]
                        if any('burst' in n.lower() for n in htoa_notes):
                            if 0 not in burst_tracks:
                                burst_tracks.append(0)
                    elif not htoa_ok and any(
                        'silent' in n.lower() or 'SKIPPED' in n for n in htoa_notes
                    ):
                        # Digitally silent pregap — not an archival failure.
                        pass
                    elif not htoa_ok:
                        raise RuntimeError(
                            next(
                                (n for n in htoa_notes if 'fail' in n.lower() or 'mismatch' in n.lower()),
                                'HTOA extract failed',
                            )
                        )
                    unit_index += 1.0

                # --- Regular tracks ---
                # Secure test&copy (retry on CRC mismatch). Failed tracks are
                # recovered individually in burst mode; other tracks stay secure.
                try:
                    album_dir = self._rip_tracks_phase(
                        job=job,
                        tracks=tracks,
                        tmp_path=tmp_path,
                        album_dir=album_dir,
                        embed_art=embed_art,
                        defeat_cache=defeat_cache,
                        expected_sizes=expected_sizes,
                        ar_verifier=ar_verifier,
                        disc_track_count=disc_track_count,
                        unit_index=unit_index,
                        total_units=total_units,
                        report=report,
                        outputs=outputs,
                        track_output_pairs=track_output_pairs,
                        ar_results=ar_results,
                        notes=notes,
                        burst_tracks=burst_tracks,
                        test_copy_mismatches=test_copy_mismatches,
                        rip_log=rip_log,
                        skipped_unverified=skipped_unverified,
                    )
                except _CrcMismatch as crc_exc:
                    if crc_exc.track_number not in test_copy_mismatches:
                        test_copy_mismatches.append(crc_exc.track_number)
                    raise RuntimeError(crc_exc.message) from crc_exc
                if self._cancelled:
                    return finish_result(
                        success=False,
                        cancelled=True,
                        error='Cancelled',
                        outputs=outputs,
                        album_dir=album_dir,
                        ar_results=ar_results,
                        notes=notes,
                        burst_tracks=burst_tracks,
                        rip_log=rip_log,
                        cover_path=cover_path,
                        htoa_ripped=htoa_ripped,
                        test_copy_mismatches=test_copy_mismatches,
                        log_dir=primary_audio_dir,
                        archival_ok=False,
                    )

                data_index = float(len(tracks) + (1 if htoa_info else 0))
                album_dirs = unique_album_dirs(outputs, album_dir)
                data_output_pairs, data_paths = self._rip_data_tracks(
                    job,
                    album_dirs,
                    notes=notes,
                    rip_log=rip_log,
                    report=lambda state, sub, msg: report(
                        0,
                        state,
                        index=data_index,
                        sub=sub,
                        message=msg,
                        total_units=total_units,
                    ),
                )
                outputs.extend(data_paths)

                # ReplayGain / Sound Check on the complete audio set
                if (
                    (job.apply_replaygain or job.write_sound_check)
                    and outputs
                    and not self._cancelled
                ):
                    loud_msg = (
                        'Analyzing ReplayGain (track + album)…'
                        if job.apply_replaygain
                        else 'Analyzing loudness for Sound Check…'
                    )
                    report(
                        tracks[-1] if tracks else 0,
                        RipState.REPLAYGAIN,
                        index=total_units - 1,
                        sub=1.0,
                        message=loud_msg,
                        total_units=total_units,
                    )
                    audio_for_rg = [
                        p
                        for p in outputs
                        if p.suffix.lower()
                        in {
                            '.flac',
                            '.wv',
                            '.mp3',
                            '.m4a',
                            '.opus',
                            '.wav',
                            '.aiff',
                            '.aif',
                        }
                    ]

                    def _rg_pulse(*, _msg=loud_msg) -> None:
                        report(
                            tracks[-1] if tracks else 0,
                            RipState.REPLAYGAIN,
                            index=total_units - 1,
                            sub=1.0,
                            message=_msg,
                            total_units=total_units,
                            nudge=True,
                        )

                    with ProgressPulse(_rg_pulse):
                        rg_notes = apply_replaygain(
                            audio_for_rg or outputs,
                            write_rg_tags=job.apply_replaygain,
                            write_sound_check=job.write_sound_check,
                        )
                    notes.extend(rg_notes)
                    if rip_log is not None:
                        rip_log.replaygain_notes.extend(rg_notes)

                # Multi-file CUE for lossless encodes only (FLAC / WAV).
                if job.write_cue_file and track_output_pairs and not self._cancelled:
                    by_group: dict[tuple[Path, str], list[tuple[int, Path]]] = defaultdict(list)
                    for track_no, path in track_output_pairs:
                        by_group[(path.parent, path.suffix.lower())].append((track_no, path))
                    htoa_by_dir: dict[Path, Path] = {}
                    if htoa_ripped:
                        for hp in htoa_paths or ([htoa_path] if htoa_path is not None else []):
                            htoa_by_dir[hp.parent] = hp
                    wrote_cue = False
                    for (cue_album_dir, cue_ext), pairs in by_group.items():
                        # ALAC primary uses .m4a (AAC also-encode is never in pairs).
                        if not is_lossless_extension(cue_ext) and cue_ext != '.m4a':
                            continue
                        try:
                            cue_path = self._write_track_cue(
                                job,
                                album_dir=cue_album_dir,
                                track_files=pairs,
                                htoa_path=htoa_by_dir.get(cue_album_dir),
                                ext=cue_ext,
                                data_files=[
                                    (n, cue_album_dir / p.name)
                                    for n, p in data_output_pairs
                                    if (cue_album_dir / p.name).is_file()
                                ],
                            )
                            if cue_path is not None:
                                wrote_cue = True
                                outputs.append(cue_path)
                                notes.append(f'CUE sheet: {cue_path.name}')
                                if rip_log is not None:
                                    rip_log.notes.append(f'CUE sheet (multi-file): {cue_path.name}')
                        except Exception as cue_exc:
                            log.warning('Failed to write multi-file CUE: %s', cue_exc)
                            notes.append(f'CUE sheet warning: {cue_exc}')
                    if not wrote_cue:
                        skip_note = 'CUE sheet skipped (lossless encode only)'
                        notes.append(skip_note)
                        if rip_log is not None:
                            rip_log.notes.append(skip_note)

        except Exception as exc:
            log.exception('Rip failed')
            return finish_result(
                success=False,
                error=str(exc),
                outputs=outputs,
                album_dir=album_dir,
                ar_results=ar_results,
                notes=notes,
                burst_tracks=burst_tracks,
                rip_log=rip_log,
                cover_path=cover_path,
                htoa_ripped=htoa_ripped,
                test_copy_mismatches=test_copy_mismatches,
                log_dir=primary_audio_dir,
                archival_ok=False,
            )

        if self._cancelled:
            return finish_result(
                success=False,
                cancelled=True,
                error='Cancelled',
                outputs=outputs,
                album_dir=album_dir,
                ar_results=ar_results,
                notes=notes,
                burst_tracks=burst_tracks,
                rip_log=rip_log,
                cover_path=cover_path,
                htoa_ripped=htoa_ripped,
                test_copy_mismatches=test_copy_mismatches,
                log_dir=primary_audio_dir,
                archival_ok=False,
            )


        fail_reasons, tiers = evaluate_archival(
            job,
            ar_results=ar_results,
            burst_tracks=burst_tracks,
            notes=notes,
            skipped_unverified=skipped_unverified,
            ar_lookup_error=(
                ar_verifier.fetch_error if ar_verifier is not None else None
            ),
        )
        done_msg = format_done_message(
            job=job,
            outputs=outputs,
            tiers=tiers,
            ar_results=ar_results,
            burst_tracks=burst_tracks,
            htoa_ripped=htoa_ripped,
            data_count=len(data_output_pairs),
            test_copy_mismatches=test_copy_mismatches,
        )

        error = '; '.join(fail_reasons) if fail_reasons else None
        result = finish_result(
            success=tiers.archival_ok,
            outputs=outputs,
            album_dir=album_dir,
            ar_results=ar_results,
            notes=notes,
            burst_tracks=burst_tracks,
            rip_log=rip_log,
            cover_path=cover_path,
            htoa_ripped=htoa_ripped,
            test_copy_mismatches=test_copy_mismatches,
            log_dir=primary_audio_dir,
            error=error,
            secure_ok=tiers.secure_ok,
            db_verified=tiers.db_verified,
            archival_ok=tiers.archival_ok,
            outcome_summary=tiers.summary,
        )
        if result.log_path is not None:
            done_msg = f'{done_msg} · log saved'
            notes.append(f'Rip log: {result.log_path}')

        if on_progress is not None:
            on_progress(
                RipProgress(
                    track_number=tracks[-1] if tracks else 0,
                    state=RipState.DONE,
                    fraction=1.0,
                    message=done_msg,
                )
            )
        return result
