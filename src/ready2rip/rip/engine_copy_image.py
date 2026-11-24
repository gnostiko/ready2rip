# SPDX-License-Identifier: GPL-3.0-or-later
"""CopyImageMixin methods for :class:`~ready2rip.rip.engine.RipEngine`."""

from __future__ import annotations

from ready2rip.rip._engine_shared import *  # noqa: F403

# NOTE: This mixin is composed onto RipEngine. Method bodies expect the full
# RipEngine attribute/helper surface (imports resolved in engine.py).


class CopyImageMixin:
    def _run_copy_image(
        self,
        job: RipJob,
        base: Path,
        fmt: str,
        on_progress: ProgressCallback | None,
        *,
        layout_notes: list[str] | None = None,
    ) -> RipResult:
        """Copy Image mode.

        Rips a continuous sector span to one file and encodes (FLAC/WAV).
        When *job.write_cue_file* is set, also writes a matching ``.cue``.
        Track 1 is never extended with a standard 2s pause; extended non-silent
        HTOA may start the image with INDEX 00.
        """
        notes: list[str] = list(layout_notes or [])
        disc = job.disc_info
        if disc is None or not disc.tracks:
            return RipResult(success=False, error='No disc TOC for Copy Image')

        last_overall = 0.0

        progress_lock = threading.Lock()

        def report(
            state: RipState,
            frac: float,
            message: str,
            *,
            nudge: bool = False,
        ) -> None:
            """Whole-image progress: measured *frac*, with nudge creep to 0.99."""
            nonlocal last_overall
            if on_progress is None:
                return
            measured = max(0.0, min(1.0, frac))
            with progress_lock:
                if nudge:
                    overall = max(last_overall, min(0.99, last_overall + 0.0025))
                    overall = max(overall, measured)
                else:
                    overall = measured
                last_overall = max(last_overall, min(1.0, overall))
                fraction = last_overall
            on_progress(
                RipProgress(
                    track_number=0,
                    state=state,
                    fraction=fraction,
                    message=message,
                )
            )

        # Album folder (reuse track-1 path builder for consistent layout).
        ext = self.EXTENSIONS[fmt]
        path_style = artist_style_for_container(
            job.artist_style,
            encode_format=fmt,
            m4a_feat_artists=job.m4a_feat_artists,
        )
        album_dir, _ = build_output_paths(
            base_dir=base,
            album_folder_template=job.album_folder_template,
            filename_template=job.filename_template,
            album=job.album,
            track_number=1,
            extension=ext,
            artist_style=path_style,
        )
        try:
            album_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return RipResult(success=False, error=f'Cannot create album folder: {exc}')

        report(RipState.PREPARING, 0.02, 'Preparing album folder (Copy Image)…')
        embed_art, _folder_art, cover_path, art_notes = _prepare_artwork(job, album_dir)
        notes.extend(art_notes)

        # Image span: start at track 1 INDEX 01 unless HTOA is
        # enabled *and* an extended pregap exists (then include from sector 0).
        t1 = disc.tracks[0]
        t_last = disc.tracks[-1]
        file_start = t1.start_sector
        htoa_in_image = False
        if job.rip_htoa:
            htoa = detect_htoa(disc)
            # detect_htoa already ignores ≤2s standard pause; extended → from 0
            if htoa is not None:
                file_start = 0
                htoa_in_image = True

        end_sector = t_last.start_sector + t_last.length_sectors
        if end_sector <= file_start:
            return RipResult(success=False, error='Invalid disc span for Copy Image')

        total_sectors = end_sector - file_start
        expected_bytes = 44 + total_sectors * 2352  # WAV header + CDDA

        basename = image_basename(job.album, disc)

        notes.append(
            f'Copy Image span: sectors [{file_start}, {end_sector}) '
            f'({total_sectors} sectors, {total_sectors / 75.0:.1f}s)'
        )
        if htoa_in_image:
            notes.append('Image includes track-1 pregap (HTOA region) for CUE INDEX 00')

        rip_log: RipLog | None = None
        if job.write_rip_log:
            rip_log = RipLog()
            rip_log.configure_from_job(job)
            rip_log.notes.append(
                'Mode: Copy Image' + (' + CUE sheet' if job.write_cue_file else '')
            )

        try:
            with rip_scratch_dir(job.output_directory) as tmp_path:
                test_wav = tmp_path / 'image_test.wav'
                copy_wav = tmp_path / 'image_copy.wav'

                if self._cancelled:
                    return finish_result(
                        success=False,
                        cancelled=True,
                        error='Cancelled',
                        outputs=[],
                        album_dir=album_dir,
                        ar_results=[],
                        ctdb_results=[],
                        notes=notes,
                        burst_tracks=[],
                        rip_log=rip_log,
                        archival_ok=False,
                    )

                report(
                    RipState.RIPPING,
                    0.05,
                    f'Extracting disc image ({total_sectors / 75.0 / 60.0:.1f} min)…',
                )
                # Expand TOC edges into lead-in/out when the drive overreads
                # so -O offset correction has real samples (then trim back).
                read_start, read_end = overread_span(
                    file_start,
                    end_sector,
                    sample_offset=job.sample_offset,
                    overread=job.overread,
                    at_disc_start=True,
                    at_disc_end=True,
                )
                if (read_start, read_end) != (file_start, end_sector):
                    notes.append(
                        f'Overread span: sectors [{read_start}, {read_end}) '
                        f'(TOC image [{file_start}, {end_sector}))'
                    )
                read_sectors = max(0, int(read_end) - int(read_start))
                progress_expected_bytes = 44 + read_sectors * 2352
                # Secure first. When test & copy is on, do not burst the test
                # pass — a burst test vs a secure copy is not a real check.
                span_timeout_secure = max(600, total_sectors // 5)
                span_timeout_burst = max(300, total_sectors // 15)

                def _img_span(
                    dest: Path,
                    *,
                    force_mode: str | None = None,
                    on_extract_progress: Callable[[float], None] | None = None,
                ) -> tuple[str, ParanoiaStats]:
                    return self._rip_span(
                        job.device,
                        read_start,
                        read_end,
                        dest,
                        expected_bytes=expected_bytes,
                        progress_expected_bytes=progress_expected_bytes,
                        min_ratio=job.min_wav_size_ratio,
                        burst_fallback=False,
                        force_mode=force_mode,
                        sample_offset=job.sample_offset,
                        overread=job.overread,
                        timeout_secure=span_timeout_secure,
                        timeout_burst=span_timeout_burst,
                        on_extract_progress=on_extract_progress,
                    )

                def _img_extract_progress(local: float) -> None:
                    local_c = max(0.0, min(1.0, local))
                    report(
                        RipState.RIPPING,
                        0.15 + 0.25 * local_c,
                        'Extracting disc image…',
                    )

                image_burst_recovered = False
                image_primary = job_extract_mode(job)
                if job.burst_mode or image_primary == 'burst':
                    report(RipState.RIPPING, 0.15, 'Burst extracting disc image…')
                    mode, img_stats = _img_span(
                        test_wav,
                        force_mode='burst',
                        on_extract_progress=_img_extract_progress,
                    )
                    notes.append('Disc image extracted in burst mode (-Z)')
                else:
                    report(RipState.RIPPING, 0.15, 'Extracting disc image…')
                    try:
                        mode, img_stats = _img_span(
                            test_wav,
                            force_mode=image_primary,
                            on_extract_progress=_img_extract_progress,
                        )
                    except Exception as first_exc:
                        if not job.burst_fallback or self._cancelled:
                            raise
                        report(
                            RipState.RIPPING,
                            0.15,
                            'Secure image extract failed — burst recovery…',
                        )
                        mode, img_stats = _img_span(
                            test_wav,
                            force_mode='burst',
                            on_extract_progress=_img_extract_progress,
                        )
                        image_burst_recovered = True
                        notes.append(
                            f'Secure image extract failed ({first_exc}); recovered in burst mode'
                        )
                report(RipState.RIPPING, 0.40, 'Disc image extracted…')
                trim_wav_to_expected(test_wav, expected_bytes)
                if mode == 'burst' or image_burst_recovered:
                    mode = 'burst'
                    if not job.burst_mode:
                        notes.append(
                            'Disc image extracted in burst mode '
                            '(secure mode failed; disc may be scratched)'
                        )

                use_wav = test_wav
                # Burst mode / burst-recovered first pass skips test&copy CRC theater.
                if (
                    job.test_and_copy
                    and not job.burst_mode
                    and not image_burst_recovered
                    and not self._cancelled
                ):
                    max_img_retries = max(0, int(job.test_copy_max_retries))
                    img_attempts = 1 + max_img_retries
                    attempt_n = {'n': 0}
                    copy_mode = mode

                    def _img_flush() -> None:
                        if not job.defeat_audio_cache:
                            return
                        report(
                            RipState.RIPPING,
                            max(last_overall, 0.31),
                            'Defeating drive cache…',
                        )
                        flush_drive_cache(
                            job.device,
                            disc,
                            avoid_start=file_start,
                            avoid_end=end_sector,
                        )

                    def _extract_test(dest: Path) -> str:
                        extract_base = 0.66
                        report(
                            RipState.RIPPING,
                            extract_base,
                            f'Retrying disc image test & copy '
                            f'(attempt {attempt_n["n"] + 1})…',
                        )
                        self._unlink_quiet(test_wav, copy_wav)
                        flush_drive_cache(
                            job.device,
                            disc,
                            avoid_start=file_start,
                            avoid_end=end_sector,
                        )

                        def _img_retry_progress(local: float, *, _base=extract_base) -> None:
                            local_c = max(0.0, min(1.0, local))
                            report(
                                RipState.RIPPING,
                                _base + 0.20 * local_c,
                                'Retrying disc image…',
                            )

                        mode_l, retry_stats = _img_span(
                            dest,
                            force_mode='secure',
                            on_extract_progress=_img_retry_progress,
                        )
                        img_stats.merge(retry_stats)
                        report(
                            RipState.RIPPING,
                            last_overall,
                            'Trimming disc image…',
                        )
                        return mode_l

                    def _after_test(path: Path) -> None:
                        trim_wav_to_expected(path, expected_bytes)
                        report(
                            RipState.RIPPING,
                            max(last_overall, 0.30),
                            'Checking image CRC…',
                        )

                    def _extract_copy(dest: Path) -> str:
                        copy_base = 0.32 if attempt_n['n'] == 0 else 0.75
                        report(
                            RipState.RIPPING,
                            copy_base,
                            'Copy pass for disc image…',
                        )

                        def _img_copy_progress(local: float, *, _base=copy_base) -> None:
                            local_c = max(0.0, min(1.0, local))
                            report(
                                RipState.RIPPING,
                                _base + 0.20 * local_c,
                                'Copy pass for disc image…',
                            )

                        report(
                            RipState.RIPPING,
                            0.45,
                            'Copy pass for disc image…',
                        )
                        mode_l, copy_stats = _img_span(
                            dest,
                            force_mode='secure',
                            on_extract_progress=_img_copy_progress,
                        )
                        img_stats.merge(copy_stats)
                        report(
                            RipState.RIPPING,
                            0.55,
                            'Disc image copy complete…',
                        )
                        attempt_n['n'] += 1
                        return mode_l

                    def _after_copy(path: Path) -> None:
                        trim_wav_to_expected(path, expected_bytes)
                        report(
                            RipState.RIPPING,
                            0.58,
                            'Checking image CRC…',
                        )

                    def _extract_burst(dest: Path) -> str:
                        def _img_burst_progress(local: float) -> None:
                            local_c = max(0.0, min(1.0, local))
                            report(
                                RipState.RIPPING,
                                max(last_overall, 0.84 + 0.10 * local_c),
                                'Burst recovery for disc image…',
                            )

                        mode_l, burst_stats = _img_span(
                            dest,
                            force_mode='burst',
                            on_extract_progress=_img_burst_progress,
                        )
                        img_stats.merge(burst_stats)
                        return mode_l

                    def _after_burst(path: Path) -> None:
                        report(
                            RipState.RIPPING,
                            last_overall,
                            'Trimming disc image…',
                        )
                        trim_wav_to_expected(path, expected_bytes)

                    def _test_crc_tick() -> None:
                        report(
                            RipState.RIPPING,
                            last_overall,
                            'Checking image CRC…',
                            nudge=True,
                        )

                    def _copy_crc_tick() -> None:
                        report(
                            RipState.RIPPING,
                            last_overall,
                            'Checking image CRC…',
                            nudge=True,
                        )

                    def _on_mismatch(t_crc: str, c_crc: str, img_attempt: int) -> None:
                        notes.append(
                            f'Image test/copy CRC mismatch '
                            f'({t_crc} ≠ {c_crc})'
                            + ('; retrying' if img_attempt + 1 < img_attempts else '')
                        )

                    def _on_before_burst() -> None:
                        report(
                            RipState.RIPPING,
                            max(last_overall, 0.84),
                            'Image test & copy failed — burst recovery…',
                        )
                        # Always seek elsewhere before burst recovery (not only when
                        # defeat_audio_cache is on for the secure dual-pass).
                        flush_drive_cache(
                            job.device,
                            disc,
                            avoid_start=file_start,
                            avoid_end=end_sector,
                        )

                    # Attempt 0 reuses the already-extracted/trimmed test_wav.
                    report(
                        RipState.RIPPING,
                        max(last_overall, 0.30),
                        'Checking image CRC…',
                    )
                    session = run_secure_test_and_copy(
                        test_wav=test_wav,
                        copy_wav=copy_wav,
                        max_retries=max_img_retries,
                        burst_fallback=bool(job.burst_fallback),
                        extract_test=_extract_test,
                        extract_copy=_extract_copy,
                        extract_burst=_extract_burst if job.burst_fallback else None,
                        flush=_img_flush if job.defeat_audio_cache else None,
                        is_cancelled=lambda: self._cancelled,
                        unlink=self._unlink_quiet,
                        reuse_initial_test=True,
                        after_test=_after_test,
                        after_copy=_after_copy,
                        after_burst=_after_burst,
                        test_crc_tick=_test_crc_tick,
                        copy_crc_tick=_copy_crc_tick,
                        on_mismatch=_on_mismatch,
                        on_before_burst=_on_before_burst,
                        unlink_test_after_crc=False,
                        label='Image',
                    )
                    test_crc = session.secure_test_crc or session.test_crc
                    copy_crc = session.secure_copy_crc or session.copy_crc
                    copy_mode = session.copy_mode or mode
                    if session.burst_recovered:
                        mode = 'burst'
                        notes.append(
                            f'Image test/copy CRC mismatch '
                            f'({test_crc} ≠ {copy_crc}); recovered in burst'
                        )
                    elif not session.matched:
                        return finish_result(
                            success=False,
                            error=(
                                f'Image test/copy CRC mismatch '
                                f'({test_crc} ≠ {copy_crc}); '
                                f'secure retries exhausted'
                            ),
                            outputs=[],
                            album_dir=album_dir,
                            ar_results=[],
                            ctdb_results=[],
                            notes=notes,
                            burst_tracks=[],
                            rip_log=rip_log,
                            archival_ok=False,
                            secure_ok=False,
                        )
                    if copy_mode == 'burst':
                        mode = 'burst'
                        notes.append('Disc image used burst mode')
                    if test_crc and copy_crc:
                        crc_note = (
                            f'Image Test CRC {test_crc.upper()} / '
                            f'Copy CRC {copy_crc.upper()}'
                            + (
                                ' — matched'
                                if session.matched
                                else ' — MISMATCH'
                            )
                        )
                        notes.append(crc_note)
                        if rip_log is not None:
                            rip_log.notes.append(crc_note)
                    self._unlink_quiet(test_wav)
                    use_wav = copy_wav

                for line in img_stats.summary_lines(length_sectors=total_sectors):
                    notes.append(line)
                if mode == 'burst' and not job.burst_mode:
                    notes.append('Burst fallback (-Z) used for disc image')
                if rip_log is not None:
                    for line in img_stats.summary_lines(length_sectors=total_sectors):
                        rip_log.notes.append(f'Image: {line}')
                    if mode == 'burst':
                        if job.burst_mode:
                            rip_log.notes.append('Image: burst mode (-Z) for disc image')
                        else:
                            rip_log.notes.append(
                                'Image: secure paranoia failed — '
                                'burst (-Z) used for disc image'
                            )

                if self._cancelled:
                    return finish_result(
                        success=False,
                        cancelled=True,
                        error='Cancelled',
                        outputs=[],
                        album_dir=album_dir,
                        ar_results=[],
                        ctdb_results=[],
                        notes=notes,
                        burst_tracks=[],
                        rip_log=rip_log,
                        archival_ok=False,
                    )

                ar_results: list[AccurateRipResult] = []
                ctdb_results: list[CtdbResult] = []
                burst_tracks: list[int] = [0] if mode == 'burst' else []
                image_test_copy_ok = bool(
                    job.test_and_copy and not job.burst_mode and mode != 'burst'
                )

                ar_verifier: AccurateRipVerifier | None = None
                ctdb_verifier: CtdbVerifier | None = None
                if job.verify_accuraterip:
                    ar_verifier = AccurateRipVerifier(sample_offset=0)
                    ar_status = ar_verifier.prepare(disc)
                    notes.append(ar_status)
                    ar_verifier.set_total_tracks(disc.track_count)
                    if rip_log is not None:
                        rip_log.ar_status = ar_status
                    if not job.offset_configured:
                        notes.append(
                            'WARNING: Drive sample offset was not calibrated — '
                            'AccurateRip results may be unreliable'
                        )
                    report(RipState.VERIFYING, 0.68, ar_status)

                if job.verify_ctdb or job.submit_ctdb:
                    ctdb_verifier = CtdbVerifier()
                    ctdb_status = ctdb_verifier.prepare(disc)
                    notes.append(ctdb_status)
                    ctdb_verifier.set_totals(
                        total_tracks=disc.track_count,
                        total_samples=total_audio_samples(disc),
                    )
                    if rip_log is not None:
                        rip_log.ctdb_status = ctdb_status
                    report(RipState.VERIFYING, 0.69, ctdb_status)

                if ar_verifier is not None or ctdb_verifier is not None:
                    self._verify_image_databases(
                        use_wav,
                        disc,
                        file_start=file_start,
                        extract_mode=mode,
                        image_stats=img_stats,
                        ar_verifier=ar_verifier,
                        ctdb_verifier=ctdb_verifier,
                        ar_results=ar_results,
                        ctdb_results=ctdb_results,
                        notes=notes,
                        rip_log=rip_log,
                        report=report,
                    )

                if self._cancelled:
                    return finish_result(
                        success=False,
                        cancelled=True,
                        error='Cancelled',
                        outputs=[],
                        album_dir=album_dir,
                        ar_results=ar_results,
                        ctdb_results=ctdb_results,
                        notes=notes,
                        burst_tracks=burst_tracks,
                        rip_log=rip_log,
                        archival_ok=False,
                    )

                dests = self._destinations_for_track(job, 1, filename=basename)
                self._ensure_destination_dirs(dests)
                for dest in dests:
                    notes.append(f'Encode target: {dest.format.upper()} → {dest.album_dir}')
                n_targets = max(1, len(dests))

                def _img_progress(t_i: int, enc_fmt: str, _out_path: Path) -> None:
                    enc_frac = 0.75 + (0.12 * (t_i / n_targets))
                    report(
                        RipState.ENCODING,
                        enc_frac,
                        (
                            'Writing image WAV…'
                            if enc_fmt == 'wav'
                            else (
                                'Writing image AIFF…'
                                if enc_fmt == 'aiff'
                                else f'Encoding disc image to {enc_fmt.upper()}'
                                + (f' ({t_i + 1}/{n_targets})' if n_targets > 1 else '')
                                + '…'
                            )
                        ),
                    )

                def _img_encode_activity(msg: str) -> None:
                    report(RipState.ENCODING, last_overall, msg, nudge=True)

                try:
                    output_files = self._encode_wav_to_destinations(
                        use_wav,
                        dests,
                        job,
                        track_number=1,
                        embed_art=embed_art,
                        disc_track_count=disc.track_count,
                        write_cover=True,
                        tag_as_image=True,
                        on_progress=_img_progress,
                        on_activity=_img_encode_activity,
                    )
                except Exception as enc_exc:
                    return finish_result(
                        success=False,
                        error=str(enc_exc),
                        outputs=[],
                        album_dir=dests[0].meta_dir if dests else album_dir,
                        ar_results=ar_results,
                        ctdb_results=ctdb_results,
                        notes=notes,
                        burst_tracks=burst_tracks,
                        rip_log=rip_log,
                        archival_ok=False,
                        secure_ok=not burst_tracks and not job.burst_mode,
                    )

                for dest, this_out in zip(dests, output_files, strict=True):
                    if not this_out.is_file() or this_out.stat().st_size < 1000:
                        return finish_result(
                            success=False,
                            error='Image encode produced no output',
                            outputs=output_files,
                            album_dir=dest.meta_dir,
                            ar_results=ar_results,
                            ctdb_results=ctdb_results,
                            notes=notes,
                            burst_tracks=burst_tracks,
                            rip_log=rip_log,
                            archival_ok=False,
                            secure_ok=not burst_tracks and not job.burst_mode,
                        )

                album_dirs = unique_album_dirs(output_files, dests[0].meta_dir)
                data_pairs, data_paths = self._rip_data_tracks(
                    job,
                    album_dirs,
                    notes=notes,
                    rip_log=rip_log,
                    report=lambda state, sub, msg: report(state, 0.88 + 0.06 * sub, msg),
                )
                output_files.extend(data_paths)

                for dest in dests:
                    encoded = [
                        p
                        for p in output_files
                        if p.parent == dest.album_dir
                        and (
                            is_lossless_extension(p.suffix)
                            or (p.suffix.lower() == '.m4a' and dest.format == 'alac')
                        )
                        and p.stem == basename
                    ]
                    if not encoded:
                        continue
                    this_image = encoded[0]
                    if job.write_cue_file:
                        this_cue = dest.album_dir / f'{basename}.cue'
                        report(
                            RipState.ENCODING,
                            0.95,
                            'Writing CUE sheet…',
                        )
                        write_cue_sheet(
                            this_cue,
                            image_filename=this_image.name,
                            file_type=cue_file_type_for_extension(dest.extension),
                            disc=disc,
                            album=job.album,
                            file_start_sector=file_start,
                            htoa_index00=htoa_in_image,
                            data_files=[
                                (n, dest.album_dir / p.name)
                                for n, p in data_pairs
                                if (dest.album_dir / p.name).is_file()
                            ],
                        )
                        notes.append(f'CUE sheet: {this_cue.name}')
                        output_files.append(this_cue)
                        if rip_log is not None:
                            rip_log.notes.append(f'CUE sheet (image): {this_cue.name}')

                if not job.write_cue_file:
                    notes.append('CUE sheet skipped (Write .cue file is off)')

                primary_album = dests[0].album_dir
                primary_out = output_files[0] if output_files else None
                album_dir = primary_album

                if (
                    (job.apply_replaygain or job.write_sound_check)
                    and output_files
                    and not self._cancelled
                ):
                    loud_msg = (
                        'Analyzing ReplayGain (track + album)…'
                        if job.apply_replaygain
                        else 'Analyzing loudness for Sound Check…'
                    )
                    report(RipState.REPLAYGAIN, 0.94, loud_msg)
                    audio_for_rg = [
                        p
                        for p in output_files
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

                    def _img_rg_pulse(*, _msg=loud_msg) -> None:
                        report(RipState.REPLAYGAIN, 0.94, _msg, nudge=True)

                    with ProgressPulse(_img_rg_pulse):
                        rg_notes = apply_replaygain(
                            audio_for_rg or [p for p in output_files if p.suffix.lower() != '.cue'],
                            write_rg_tags=job.apply_replaygain,
                            write_sound_check=job.write_sound_check,
                        )
                    notes.extend(rg_notes)
                    if rip_log is not None:
                        rip_log.replaygain_notes.extend(rg_notes)

                if job.submit_ctdb and ctdb_verifier is not None and ctdb_results:
                    report(RipState.VERIFYING, 0.97, 'Submitting to CTDB…')
                    drivename = ''
                    try:
                        from ready2rip.disc.drive_info import probe_drive

                        drivename = probe_drive(job.device, allow_optical=False).ctdb_drivename
                    except Exception:
                        drivename = ''
                    album = job.album
                    ctdb_quality = ctdb_submit_quality(
                        test_and_copy=job.test_and_copy and not job.burst_mode,
                        burst_tracks=burst_tracks,
                        test_copy_mismatches=([] if image_test_copy_ok else [0]),
                        track_entries=(rip_log.tracks if rip_log is not None else None),
                        image_stats=img_stats,
                        image_sectors=total_sectors,
                    )
                    if ctdb_quality < 100:
                        notes.append(
                            'CTDB submit quality=0 '
                            '(requires secure test&copy, no burst, '
                            'paranoia quality 100%)'
                        )
                    submit = ctdb_verifier.submit(
                        ctdb_results,
                        drivename=drivename,
                        artist=(album.artist if album else '') or '',
                        title=(album.title if album else '') or '',
                        barcode=(album.barcode if album else '') or '',
                        quality=ctdb_quality,
                    )
                    notes.append(submit.message)
                    if rip_log is not None:
                        rip_log.notes.append(submit.message)
                    if not submit.ok and any(
                        r.confidence == CtdbConfidence.MISMATCH for r in ctdb_results
                    ):
                        try:
                            probe = ctdb_verifier.attempt_repair_for_mismatch(ctdb_results)
                            notes.append(probe.message)
                            if rip_log is not None:
                                rip_log.notes.append(probe.message)
                        except Exception as exc:
                            log.warning('CTDB repair probe failed: %s', exc)

                done_label = primary_out.name if primary_out else 'image'
                if job.write_cue_file and primary_out is not None:
                    done_label = f'{primary_out.name} + {basename}.cue'
                if len(dests) > 1:
                    done_label = f'{done_label} · {len(dests)} encodes'
                fail_reasons, tiers = evaluate_archival(
                    job,
                    ar_results=ar_results,
                    ctdb_results=ctdb_results,
                    burst_tracks=burst_tracks,
                    notes=notes,
                    ar_lookup_error=(
                        ar_verifier.fetch_error if ar_verifier is not None else None
                    ),
                    ctdb_lookup_error=(
                        ctdb_verifier.lookup_error
                        if ctdb_verifier is not None
                        else None
                    ),
                )
                done_msg = format_done_message(
                    job=job,
                    outputs=output_files,
                    tiers=tiers,
                    ar_results=ar_results,
                    ctdb_results=ctdb_results,
                    burst_tracks=burst_tracks,
                    htoa_ripped=htoa_in_image,
                    data_count=len(data_pairs),
                    test_copy_mismatches=([] if image_test_copy_ok else [0]),
                    copy_image=True,
                    image_label=done_label,
                )
                result = finish_result(
                    success=tiers.archival_ok,
                    outputs=output_files,
                    album_dir=primary_album,
                    ar_results=ar_results,
                    ctdb_results=ctdb_results,
                    notes=notes,
                    burst_tracks=burst_tracks,
                    rip_log=rip_log,
                    cover_path=cover_path,
                    htoa_ripped=htoa_in_image,
                    error=('; '.join(fail_reasons) if fail_reasons else None),
                    secure_ok=tiers.secure_ok,
                    db_verified=tiers.db_verified,
                    archival_ok=tiers.archival_ok,
                    outcome_summary=tiers.summary,
                    log_dir=primary_album,
                )
                if result.log_path is not None:
                    done_msg = f'{done_msg} · log saved'
                report(RipState.DONE, 1.0, done_msg)
                return result
        except Exception as exc:
            log.exception('Copy Image failed')
            return finish_result(
                success=False,
                error=str(exc),
                outputs=[],
                album_dir=album_dir,
                ar_results=[],
                ctdb_results=[],
                notes=notes,
                burst_tracks=[],
                rip_log=rip_log,
                cancelled=self._cancelled,
                archival_ok=False,
                secure_ok=False,
            )

