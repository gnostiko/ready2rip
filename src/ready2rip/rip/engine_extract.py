# SPDX-License-Identifier: GPL-3.0-or-later
"""ExtractMixin methods for :class:`~ready2rip.rip.engine.RipEngine`."""

from __future__ import annotations

from ready2rip.rip._engine_shared import *  # noqa: F403

# NOTE: This mixin is composed onto RipEngine. Method bodies expect the full
# RipEngine attribute/helper surface (imports resolved in engine.py).


class ExtractMixin:
    def _rip_htoa_unit(
        self,
        *,
        job: RipJob,
        htoa_info,
        tmp_path: Path,
        embed_art: EmbedArtworkSet,
        defeat_cache: bool,
        rip_log: RipLog | None,
        report: Callable,
    ) -> tuple[bool, Path | None, list[Path], list[str]]:
        """Test & copy HTOA; skip if digitally silent.

        Returns ``(ok, primary_path, all_paths, notes)``.
        """
        notes: list[str] = []
        test_wav = tmp_path / 'htoa_test.wav'
        copy_wav = tmp_path / 'htoa_copy.wav'
        title = htoa_display_title()

        log_entry = None
        if rip_log is not None:
            log_entry = rip_log.begin_track(
                0,
                title=title,
                length_label=htoa_info.duration_label,
                start_sector=0,
                length_sectors=htoa_info.length_sectors,
            )

        htoa_mode = job_extract_mode(job)
        try:
            report(
                RipState.RIPPING,
                0.05,
                (
                    f'Burst extracting HTOA ({htoa_info.duration_label})…'
                    if job.burst_mode
                    else f'Extracting HTOA ({htoa_info.duration_label})…'
                ),
            )
            extract_htoa(
                job.device,
                htoa_info,
                test_wav,
                mode=htoa_mode,
                sample_offset=job.sample_offset,
            )
            if job.burst_mode:
                notes.append('HTOA extracted in burst mode')
        except Exception as exc:
            if job.burst_mode:
                notes.append(f'HTOA extract failed: {exc}')
                if log_entry is not None:
                    log_entry.status = 'FAILED'
                    log_entry.notes.append(str(exc))
                return False, None, [], notes
            if job.burst_fallback:
                try:
                    extract_htoa(
                        job.device,
                        htoa_info,
                        test_wav,
                        mode='burst',
                        sample_offset=job.sample_offset,
                    )
                    notes.append('HTOA extracted in burst mode')
                except Exception as exc2:
                    notes.append(f'HTOA extract failed: {exc2}')
                    if log_entry is not None:
                        log_entry.status = 'FAILED'
                        log_entry.notes.append(str(exc2))
                    return False, None, [], notes
            else:
                notes.append(f'HTOA extract failed: {exc}')
                if log_entry is not None:
                    log_entry.status = 'FAILED'
                    log_entry.notes.append(str(exc))
                return False, None, [], notes

        if is_digitally_silent(test_wav):
            notes.append(
                f'HTOA pregap is digitally silent ({htoa_info.length_sectors} sectors) — not saved'
            )
            if log_entry is not None:
                log_entry.status = 'SKIPPED (silence)'
                log_entry.notes.append('Digitally silent pregap')
            try:
                test_wav.unlink(missing_ok=True)
            except OSError:
                pass
            return False, None, [], notes

        notes.append(f'Non-silent HTOA detected ({htoa_info.duration_label}) — ripping')

        test_crc = ''
        copy_crc = ''
        peak = None
        mode = job_extract_mode(job)

        if job.test_and_copy and not job.burst_mode:
            test_crc = wav_pcm_crc(test_wav)
            self._unlink_quiet(test_wav)
            if defeat_cache:
                report(RipState.RIPPING, 0.25, 'Defeating drive cache before HTOA copy…')
                flush_drive_cache(
                    job.device,
                    job.disc_info,
                    avoid_start=0,
                    avoid_end=htoa_info.length_sectors,
                )
            report(RipState.RIPPING, 0.35, 'Copying HTOA…')
            try:
                extract_htoa(
                    job.device,
                    htoa_info,
                    copy_wav,
                    mode='secure',
                    sample_offset=job.sample_offset,
                )
            except Exception:
                if job.burst_fallback:
                    extract_htoa(
                        job.device,
                        htoa_info,
                        copy_wav,
                        mode='burst',
                        sample_offset=job.sample_offset,
                    )
                    mode = 'burst'
                else:
                    raise
            copy_crc, peak, _ = analyze_wav_for_log(copy_wav, htoa_info.length_sectors)
            if test_crc != copy_crc:
                max_htoa_retries = max(0, int(job.test_copy_max_retries))
                matched_htoa = False
                for htoa_attempt in range(max_htoa_retries):
                    if defeat_cache:
                        flush_drive_cache(
                            job.device,
                            job.disc_info,
                            avoid_start=0,
                            avoid_end=htoa_info.length_sectors,
                        )
                    # Re-run both passes so CRCs are from the same secure pair.
                    extract_htoa(
                        job.device,
                        htoa_info,
                        test_wav,
                        mode='secure',
                        sample_offset=job.sample_offset,
                    )
                    test_crc = wav_pcm_crc(test_wav)
                    self._unlink_quiet(test_wav)
                    if defeat_cache:
                        flush_drive_cache(
                            job.device,
                            job.disc_info,
                            avoid_start=0,
                            avoid_end=htoa_info.length_sectors,
                        )
                    extract_htoa(
                        job.device,
                        htoa_info,
                        copy_wav,
                        mode='secure',
                        sample_offset=job.sample_offset,
                    )
                    copy_crc, peak, _ = analyze_wav_for_log(copy_wav, htoa_info.length_sectors)
                    if test_crc and copy_crc and test_crc == copy_crc:
                        matched_htoa = True
                        notes.append(f'HTOA test/copy CRC matched on retry {htoa_attempt + 1}')
                        break
                if not matched_htoa and test_crc != copy_crc:
                    if job.burst_fallback:
                        if defeat_cache:
                            flush_drive_cache(
                                job.device,
                                job.disc_info,
                                avoid_start=0,
                                avoid_end=htoa_info.length_sectors,
                            )
                        extract_htoa(
                            job.device,
                            htoa_info,
                            copy_wav,
                            mode='burst',
                            sample_offset=job.sample_offset,
                        )
                        mode = 'burst'
                        copy_crc, peak, _ = analyze_wav_for_log(copy_wav, htoa_info.length_sectors)
                        notes.append(
                            f'HTOA test/copy CRC mismatch '
                            f'(test={test_crc} copy={copy_crc}); '
                            f'recovered in burst'
                        )
                    else:
                        raise RuntimeError(
                            f'HTOA test/copy CRC mismatch '
                            f'(test={test_crc} copy={copy_crc}); '
                            f'secure retries exhausted'
                        )
            wav_for_encode = copy_wav
        else:
            wav_for_encode = test_wav
            copy_crc, peak, _ = analyze_wav_for_log(test_wav, htoa_info.length_sectors)
            test_crc = copy_crc

        safe_title = ''.join(c if c not in '<>:"/\\|?*' else '_' for c in title)
        dests = self._destinations_for_track(job, 1, filename=f'00 - {safe_title}')
        self._ensure_destination_dirs(dests)
        n_targets = max(1, len(dests))

        def _htoa_progress(t_i: int, enc_fmt: str, _out_path: Path) -> None:
            enc_frac = 0.7 + (0.15 * (t_i / n_targets))
            report(
                RipState.ENCODING,
                enc_frac,
                f'Encoding HTOA to {enc_fmt.upper()}'
                + (f' ({t_i + 1}/{n_targets})' if n_targets > 1 else '')
                + '…',
            )

        htoa_outputs = self._encode_wav_to_destinations(
            wav_for_encode,
            dests,
            job,
            track_number=0,
            embed_art=embed_art,
            disc_track_count=job.disc_track_count or 0,
            retitle=title,
            write_cover=True,
            on_progress=_htoa_progress,
        )
        self._unlink_quiet(test_wav, copy_wav)
        primary_out = htoa_outputs[0] if htoa_outputs else None

        if primary_out is None:
            return False, None, [], notes

        if log_entry is not None:
            if job.test_and_copy and test_crc and copy_crc:
                log_entry.extract_mode = f'{mode} (test & copy)'
                log_entry.test_crc = test_crc
                log_entry.copy_crc = copy_crc
                log_entry.crc_verified = test_crc == copy_crc
            else:
                log_entry.extract_mode = mode
                log_entry.test_crc = ''
                log_entry.copy_crc = copy_crc or test_crc
                log_entry.crc_verified = None
            log_entry.peak_percent = peak
            log_entry.output_path = primary_out
            log_entry.filename = primary_out.name
            log_entry.status = 'OK'
            log_entry.notes.append('Hidden Track One Audio (non-silent pregap)')
            if job.test_and_copy and test_crc and copy_crc and test_crc != copy_crc:
                log_entry.notes.append(
                    f'HTOA test/copy CRC mismatch kept copy '
                    f'(test={test_crc.upper()} copy={copy_crc.upper()})'
                )
                log_entry.had_errors = True

        saved = ', '.join(p.name for p in htoa_outputs)
        notes.append(f'HTOA saved: {saved}')
        report(
            RipState.DONE,
            1.0,
            f'Finished HTOA → {primary_out.name}',
        )
        return True, primary_out, htoa_outputs, notes

    def _write_track_cue(
        self,
        job: RipJob,
        *,
        album_dir: Path,
        track_files: list[tuple[int, Path]],
        htoa_path: Path | None,
        ext: str,
        data_files: list[tuple[int, Path]] | None = None,
    ) -> Path | None:
        """Write multi-file CUE for per-track outputs. Returns cue path or None."""
        pairs = [(n, p) for n, p in track_files if n > 0]
        if not pairs:
            return None

        basename = multi_file_cue_basename(job.album, job.disc_info)
        # Multi-disc per-folder sheets are plain CD1.cue; single-disc keeps
        # the encode format in the name when useful (Artist - Album.flac.cue).
        fmt = (ext or pairs[0][1].suffix).lstrip('.').lower() or 'audio'
        if basename.startswith('CD') and basename[2:].isdigit():
            cue_path = album_dir / f'{basename}.cue'
        else:
            cue_path = album_dir / f'{basename}.{fmt}.cue'
        write_multi_file_cue_sheet(
            cue_path,
            track_files=pairs,
            file_type=cue_file_type_for_extension(ext),
            disc=job.disc_info,
            album=job.album,
            htoa_file=htoa_path if htoa_path is not None else None,
            data_files=data_files or [],
        )
        return cue_path

    def _detect_disc_layout(
        self,
        job: RipJob,
        on_progress: ProgressCallback | None,
    ) -> list[str]:
        """Attach ioctl TOC + INDEX 00 pregaps. Non-fatal on failure."""
        notes: list[str] = []
        disc = job.disc_info
        if disc is None:
            return notes
        if on_progress is not None:
            on_progress(
                RipProgress(
                    track_number=0,
                    state=RipState.PREPARING,
                    fraction=0.0,
                    message='Detecting pregaps…',
                )
            )
        try:
            attach_mmc_toc(disc)
            source = enrich_pregaps(job.device, disc)
            notes.append(f'Pregap detection: {source or "none"}')
            n_gaps = sum(1 for t in disc.tracks if t.pregap_sectors)
            if n_gaps:
                notes.append(f'INDEX 00 pregaps on {n_gaps} audio track(s)')
            if disc.data_tracks:
                parts = ', '.join(
                    f'#{d.number} {d.cue_mode} session {d.session}' for d in disc.data_tracks
                )
                notes.append(f'Data tracks: {parts}')
        except Exception as exc:
            log.warning('Disc layout detect failed: %s', exc)
            notes.append(f'Pregap detection failed: {exc}')
        return notes

    def _rip_data_tracks(
        self,
        job: RipJob,
        album_dirs: list[Path],
        *,
        notes: list[str],
        rip_log: RipLog | None,
        report: Callable | None = None,
    ) -> tuple[list[tuple[int, Path]], list[Path]]:
        """Extract mixed-mode / CD-Extra data into lossless album folder(s).

        Lossy Also-encode directories are never given a ``.bin`` copy. Returns
        ``(primary_pairs, all_written_paths)``. Raises if ripping data is
        enabled and extract fails.
        """
        disc = job.disc_info
        if not job.rip_data_tracks or disc is None or not disc.data_tracks or not album_dirs:
            return [], []
        if self._cancelled:
            return [], []

        primary = album_dirs[0]
        n_tracks = max(1, len(disc.data_tracks))

        def on_progress(number: int, done: int, total: int, message: str) -> None:
            if report is None:
                return
            idx = 0.0
            for i, dt in enumerate(disc.data_tracks):
                if dt.number == number:
                    idx = i / n_tracks
                    break
            sub = idx + (done / max(1, total)) / n_tracks
            report(RipState.RIPPING, sub, message)

        def data_flush() -> None:
            # Seek away from whatever data span was just read.
            for dt in disc.data_tracks:
                flush_drive_cache(
                    job.device,
                    disc,
                    avoid_start=dt.start_sector,
                    avoid_end=dt.start_sector + dt.length_sectors,
                )
                break

        try:
            if report is not None:
                report(
                    RipState.RIPPING,
                    0.0,
                    (
                        'Extracting data tracks (test & copy)…'
                        if job.test_and_copy
                        else 'Extracting data tracks…'
                    ),
                )
            extracted = extract_data_tracks(
                job.device,
                disc.data_tracks,
                primary,
                on_progress=on_progress,
                cancelled=lambda: self._cancelled,
                test_and_copy=job.test_and_copy,
                max_retries=job.test_copy_max_retries,
                flush=data_flush if job.test_and_copy else None,
            )
        except Exception as exc:
            raise RuntimeError(f'Data track extract failed: {exc}') from exc

        pairs: list[tuple[int, Path]] = []
        all_paths: list[Path] = []
        updated: list = []
        for item in extracted:
            pairs.append((item.track.number, item.path))
            all_paths.append(item.path)
            updated.append(item.track)
            notes.append(item.message)
            if item.retries:
                notes.append(
                    f'Data track {item.track.number:02d}: {item.retries} sector retry(ies)'
                )
            if rip_log is not None:
                entry = rip_log.begin_track(
                    item.track.number,
                    title=f'Data ({item.track.cue_mode})',
                    length_label=item.track.duration_label,
                    start_sector=item.track.start_sector,
                    length_sectors=item.track.length_sectors,
                )
                entry.filename = item.path.name
                entry.output_path = item.path
                entry.extract_mode = (
                    f'raw {item.track.cue_mode} (test & copy)'
                    if item.test_crc
                    else f'raw {item.track.cue_mode}'
                )
                entry.test_crc = item.test_crc
                entry.copy_crc = item.crc32
                entry.crc_verified = item.matched if item.test_crc else None
                entry.status = 'OK'
                entry.notes.append(item.message)
        disc.data_tracks = updated

        for dest in album_dirs[1:]:
            dest.mkdir(parents=True, exist_ok=True)
            for _num, src in pairs:
                copied = dest / src.name
                if copied.resolve() != src.resolve():
                    shutil.copy2(src, copied)
                    all_paths.append(copied)
        return pairs, all_paths

    def _rip_tracks_phase(
        self,
        *,
        job: RipJob,
        tracks: list[int],
        tmp_path: Path,
        album_dir: Path | None,
        embed_art: EmbedArtworkSet,
        defeat_cache: bool,
        expected_sizes: dict[int, int],
        ar_verifier: AccurateRipVerifier | None,
        ctdb_verifier: CtdbVerifier | None,
        disc_track_count: int,
        unit_index: float,
        total_units: float,
        report: TrackReportFn,
        outputs: list[Path],
        track_output_pairs: list[tuple[int, Path]],
        ar_results: list[AccurateRipResult],
        ctdb_results: list[CtdbResult],
        notes: list[str],
        burst_tracks: list[int],
        test_copy_mismatches: list[int],
        rip_log: RipLog | None,
        skipped_unverified: list[int] | None = None,
    ) -> Path | None:
        """Process every selected track with secure test & copy.

        Encode of track N overlaps extraction of track N+1 (drive-bound rip
        with CPU-bound encode). AccurateRip and CTDB share one PCM load.
        """
        skipped_unverified = skipped_unverified if skipped_unverified is not None else []
        pending: _PendingEncode | None = None
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix='r2r-enc') as pool:

            def finish_pending(*, abandon: bool = False) -> Path | None:
                nonlocal pending, album_dir
                if pending is None:
                    return album_dir
                job_pending = pending
                pending = None
                self._encode_pending = None
                if abandon or self._cancelled:
                    written: list[Path] | None = None
                    try:
                        written = job_pending.future.result(timeout=120)
                    except Exception:
                        written = None
                    if written:
                        # Encode finished — keep outputs (later-track failure or
                        # cancel after a completed encode both leave a useful rip).
                        primary_out: Path | None = None
                        for dest, out_path in zip(job_pending.dests, written, strict=True):
                            outputs.append(out_path)
                            if dest.primary:
                                track_output_pairs.append((job_pending.track_no, out_path))
                                if primary_out is None:
                                    primary_out = out_path
                                    album_dir = dest.meta_dir
                        self._unlink_quiet(job_pending.test_wav, job_pending.copy_wav)
                        if job_pending.log_entry is not None and primary_out is not None:
                            job_pending.log_entry.output_path = primary_out
                            job_pending.log_entry.filename = primary_out.name
                            if job_pending.log_entry.status != 'finished with errors':
                                job_pending.log_entry.status = 'OK'
                        return album_dir
                    # Incomplete / failed encode — remove any partial outputs.
                    for dest in job_pending.dests:
                        try:
                            dest.out_path.unlink(missing_ok=True)
                        except OSError:
                            pass
                    self._unlink_quiet(job_pending.test_wav, job_pending.copy_wav)
                    return album_dir

                # Blocking join — keep the banner honest while the encoder
                # finishes (extract of the next track already completed).
                def _wait_encode_tick(
                    *,
                    _tn=job_pending.track_no,
                    _idx=job_pending.idx,
                ) -> None:
                    report(
                        _tn,
                        RipState.ENCODING,
                        index=_idx,
                        sub=0.95,
                        message=f'Finishing encode for track {_tn}…',
                        total_units=total_units,
                        nudge=True,
                    )

                if not job_pending.future.done():
                    report(
                        job_pending.track_no,
                        RipState.ENCODING,
                        index=job_pending.idx,
                        sub=0.95,
                        message=(f'Finishing encode for track {job_pending.track_no}…'),
                        total_units=total_units,
                    )
                    with ProgressPulse(_wait_encode_tick):
                        written = job_pending.future.result()
                else:
                    written = job_pending.future.result()
                primary_out: Path | None = None
                for dest, out_path in zip(job_pending.dests, written, strict=True):
                    outputs.append(out_path)
                    if dest.primary:
                        track_output_pairs.append((job_pending.track_no, out_path))
                        if primary_out is None:
                            primary_out = out_path
                            album_dir = dest.meta_dir
                self._unlink_quiet(job_pending.test_wav, job_pending.copy_wav)
                if job_pending.log_entry is not None and primary_out is not None:
                    job_pending.log_entry.output_path = primary_out
                    job_pending.log_entry.filename = primary_out.name
                    if job_pending.log_entry.status != 'finished with errors':
                        job_pending.log_entry.status = 'OK'
                done_extra = ''
                if job_pending.extracted.mode == 'burst':
                    done_extra = ' (burst)'
                elif job.test_and_copy and job_pending.extracted.copy_crc:
                    done_extra = f' (test=copy CRC {job_pending.extracted.copy_crc})'
                if len(job_pending.dests) > 1:
                    done_extra = f'{done_extra} · {len(job_pending.dests)} encodes'
                report(
                    job_pending.track_no,
                    RipState.DONE,
                    index=job_pending.idx,
                    sub=1.0,
                    message=f'Finished track {job_pending.track_no}{done_extra}',
                    total_units=total_units,
                )
                return album_dir

            for i, track_no in enumerate(tracks):
                if self._cancelled:
                    finish_pending(abandon=True)
                    return album_dir

                idx = unit_index + i
                toc_track = self._toc_track(job.disc_info, track_no)
                sectors = toc_track.length_sectors if toc_track else 0

                log_entry = None
                if rip_log is not None:
                    log_entry = rip_log.begin_track(
                        track_no,
                        title=title_for_track(job.album, track_no),
                        length_label=(toc_track.duration_label if toc_track else ''),
                        start_sector=toc_track.start_sector if toc_track else 0,
                        length_sectors=sectors,
                    )

                test_wav = tmp_path / f'track{track_no:02d}_test.wav'
                copy_wav = tmp_path / f'track{track_no:02d}_copy.wav'
                expected = expected_sizes.get(track_no)

                # Skip-existing: avoid extract when every destination is present.
                # Re-verify from the existing primary lossless when possible so
                # the album cannot "succeed" with unverified leftover files.
                early_dests = self._destinations_for_track(job, track_no)
                if (
                    normalize_existing_files_policy(job.existing_files) == 'skip'
                    and not early_dests
                ):
                    finish_pending()
                    re_ok = self._reverify_existing_track(
                        job=job,
                        track_no=track_no,
                        tmp_path=tmp_path,
                        ar_verifier=ar_verifier,
                        ctdb_verifier=ctdb_verifier,
                        ar_results=ar_results,
                        ctdb_results=ctdb_results,
                        log_entry=log_entry,
                        idx=idx,
                        total_units=total_units,
                        report=report,
                        outputs=outputs,
                        track_output_pairs=track_output_pairs,
                        notes=notes,
                    )
                    if not re_ok:
                        if track_no not in skipped_unverified:
                            skipped_unverified.append(track_no)
                        notes.append(
                            f'Skipped track {track_no} '
                            '(exists; not re-verified — non-archival)'
                        )
                        if log_entry is not None:
                            log_entry.status = 'skipped (exists, unverified)'
                    else:
                        notes.append(
                            f'Skipped track {track_no} (exists; re-verified from file)'
                        )
                        if log_entry is not None:
                            log_entry.status = 'skipped (exists, re-verified)'
                    report(
                        track_no,
                        RipState.DONE,
                        index=idx,
                        sub=1.0,
                        message=(
                            f'Skipped track {track_no} (exists'
                            + ('; re-verified)' if re_ok else '; unverified)')
                        ),
                        total_units=total_units,
                    )
                    continue

                # Extract overlaps the previous track's encode.
                extracted = self._extract_track_secure(
                    job=job,
                    track_no=track_no,
                    test_wav=test_wav,
                    copy_wav=copy_wav,
                    expected=expected,
                    sectors=sectors,
                    defeat_cache=defeat_cache,
                    idx=idx,
                    total_units=total_units,
                    report=report,
                    notes=notes,
                )
                if self._cancelled:
                    finish_pending(abandon=True)
                    return album_dir
                if job.test_and_copy and not job.burst_mode and not extracted.matched:
                    if track_no not in test_copy_mismatches:
                        test_copy_mismatches.append(track_no)
                    accept_burst = (
                        extracted.mode == 'burst'
                        and job.burst_fallback
                        and extracted.copy_wav.is_file()
                    )
                    if not accept_burst:
                        finish_pending(abandon=True)
                        raise _CrcMismatch(
                            track_no,
                            extracted.error
                            or (
                                f'Test & copy CRC mismatch on track {track_no} '
                                f'(secure retries exhausted; enable Burst '
                                f'Fallback to allow insecure recovery)'
                            ),
                            test_crc=extracted.test_crc,
                            copy_crc=extracted.copy_crc,
                            attempt_log=extracted.attempt_log,
                        )

                if extracted.mode == 'burst' and track_no not in burst_tracks:
                    burst_tracks.append(track_no)

                self._apply_extract_to_log(
                    log_entry,
                    extracted=extracted,
                    sectors=sectors,
                    expected_bytes=expected,
                    test_and_copy=job.test_and_copy,
                )

                def _verify_tick(*, _tn=track_no, _idx=idx) -> None:
                    report(
                        _tn,
                        RipState.VERIFYING,
                        index=_idx,
                        sub=0.84,
                        message=f'Verifying track {_tn}…',
                        total_units=total_units,
                        nudge=True,
                    )

                with ProgressPulse(_verify_tick):
                    self._verify_track_databases(
                        extracted.copy_wav,
                        track_no,
                        ar_verifier=ar_verifier,
                        ctdb_verifier=ctdb_verifier,
                        ar_results=ar_results,
                        ctdb_results=ctdb_results,
                        log_entry=log_entry,
                        idx=idx,
                        total_units=total_units,
                        report=report,
                    )

                report(
                    track_no,
                    RipState.DONE,
                    index=idx,
                    sub=1.0,
                    message=f'Ripped track {track_no}',
                    total_units=total_units,
                )

                if self._cancelled:
                    finish_pending(abandon=True)
                    return album_dir

                # Previous encode must finish before we start another (one WAV
                # encode worker); extract of this track already overlapped it.
                finish_pending()
                if self._cancelled:
                    return album_dir

                dests = self._destinations_for_track(job, track_no)
                if not dests:
                    # Skip-existing after extract: destinations appeared; keep
                    # the just-verified WAV results (already in ar/ctdb lists).
                    notes.append(
                        f'Skipped encode for track {track_no} '
                        '(output already exists; extract was verified)'
                    )
                    if log_entry is not None:
                        log_entry.status = 'skipped (exists, verified from extract)'
                    # Attach existing primary path so the album still lists a file.
                    probe = replace(job, existing_files='overwrite')
                    existing = self._destinations_for_track(probe, track_no)
                    if existing:
                        primary = next((d for d in existing if d.primary), existing[0])
                        if primary.out_path.is_file():
                            outputs.append(primary.out_path)
                            if primary.primary:
                                track_output_pairs.append((track_no, primary.out_path))
                                album_dir = primary.meta_dir
                            if log_entry is not None:
                                log_entry.output_path = primary.out_path
                                log_entry.filename = primary.out_path.name
                    self._unlink_quiet(extracted.test_wav, extracted.copy_wav)
                    report(
                        track_no,
                        RipState.DONE,
                        index=idx,
                        sub=1.0,
                        message=f'Skipped encode track {track_no} (exists)',
                        total_units=total_units,
                    )
                    continue

                self._ensure_destination_dirs(dests)
                n_targets = max(1, len(dests))
                report(
                    track_no,
                    RipState.ENCODING,
                    index=idx,
                    sub=0.90,
                    message=(
                        f'Encoding track {track_no}'
                        + (f' ({n_targets} formats)' if n_targets > 1 else '')
                        + '…'
                    ),
                    total_units=total_units,
                )

                def _encode_activity(msg: str, *, _tn=track_no, _idx=idx) -> None:
                    report(
                        _tn,
                        RipState.ENCODING,
                        index=_idx,
                        sub=0.92,
                        message=msg,
                        total_units=total_units,
                        nudge=True,
                    )

                future = pool.submit(
                    self._encode_wav_to_destinations,
                    extracted.copy_wav,
                    dests,
                    job,
                    track_number=track_no,
                    embed_art=embed_art,
                    disc_track_count=disc_track_count,
                    write_cover=True,
                    on_progress=None,
                    on_activity=_encode_activity,
                )
                pending = _PendingEncode(
                    future=future,
                    track_no=track_no,
                    idx=idx,
                    dests=dests,
                    extracted=extracted,
                    test_wav=extracted.test_wav,
                    copy_wav=extracted.copy_wav,
                    log_entry=log_entry,
                )
                self._encode_pending = pending

            finish_pending(abandon=self._cancelled)

        return album_dir

    def _extract_track_secure(
        self,
        *,
        job: RipJob,
        track_no: int,
        test_wav: Path,
        copy_wav: Path,
        expected: int | None,
        sectors: int,
        defeat_cache: bool,
        idx: float,
        total_units: float,
        report: TrackReportFn,
        notes: list[str],
    ) -> _TrackExtract:
        """Secure test&copy. Both passes use paranoia only; CRC retry, then per-track burst.

        When *job.burst_mode* is on, skips secure passes and uses a single -Z extract.
        """
        if job.burst_mode:
            notes.append(f'Track {track_no}: burst mode (-Z)')
            recovered = self._extract_track_burst(
                job=job,
                track_no=track_no,
                copy_wav=copy_wav,
                expected=expected,
                sectors=sectors,
                idx=idx,
                total_units=total_units,
                report=report,
            )
            # Single intentional -Z pass — treat as accepted (no test & copy).
            recovered.matched = True
            return recovered

        max_retries = max(0, int(job.test_copy_max_retries))
        attempts = 1 + max_retries if job.test_and_copy else 1

        # Archival dual-pass → secure (-X); Normal → repair-tolerant; Burst handled above.
        extract_mode = job_extract_mode(job)
        final_mode = 'secure' if job.test_and_copy else extract_mode
        test_mode = ''
        copy_mode = ''
        test_crc = ''
        copy_crc = ''
        peak: float | None = None
        elapsed = 0.0
        matched = not job.test_and_copy
        last_err: str | None = None
        track_stats = ParanoiaStats()
        attempt_log: list[str] = []
        dual_pass = bool(job.test_and_copy)

        for attempt in range(attempts):
            if self._cancelled:
                break
            try:
                attempt_stats = ParanoiaStats()
                attempt_test_mode = ''
                attempt_copy_mode = ''
                attempt_t0 = time.monotonic()

                if job.test_and_copy:
                    test_label = f'Testing track {track_no}' + (
                        f' (retry {attempt})' if attempt else ''
                    )
                    report(
                        track_no,
                        RipState.RIPPING,
                        index=idx,
                        sub=0.0,
                        message=f'{test_label}…',
                        total_units=total_units,
                    )

                    def _test_progress(local: float, *, _label=test_label) -> None:
                        local_c = max(0.0, min(1.0, local))
                        report(
                            track_no,
                            RipState.RIPPING,
                            index=idx,
                            # Test pass owns most of the first half of the track unit.
                            sub=0.32 * local_c,
                            message=f'{_label}…',
                            total_units=total_units,
                        )

                    # Secure-only: a burst test against a secure copy is not a real check.
                    attempt_test_mode, st = self._rip_track(
                        job.device,
                        track_no,
                        test_wav,
                        job=job,
                        expected_bytes=expected,
                        min_ratio=job.min_wav_size_ratio,
                        burst_fallback=False,
                        force_mode='secure',
                        sample_offset=job.sample_offset,
                        on_extract_progress=_test_progress,
                        expected_sectors=sectors,
                    )
                    attempt_stats.merge(st)
                    report(
                        track_no,
                        RipState.RIPPING,
                        index=idx,
                        sub=0.33,
                        message=f'Checking test CRC for track {track_no}…',
                        total_units=total_units,
                    )

                    def _test_crc_tick(*, _tn=track_no, _idx=idx) -> None:
                        report(
                            _tn,
                            RipState.RIPPING,
                            index=_idx,
                            sub=0.33,
                            message=f'Checking test CRC for track {_tn}…',
                            total_units=total_units,
                            nudge=True,
                        )

                    with ProgressPulse(_test_crc_tick):
                        test_crc = wav_pcm_crc(test_wav)
                    self._unlink_quiet(test_wav)
                    if defeat_cache:
                        report(
                            track_no,
                            RipState.RIPPING,
                            index=idx,
                            sub=0.33,
                            message='Defeating drive cache…',
                            total_units=total_units,
                        )
                        avoid0, avoid1 = _track_avoid_span(job.disc_info, track_no)
                        flush_drive_cache(
                            job.device,
                            job.disc_info,
                            avoid_start=avoid0,
                            avoid_end=avoid1,
                        )

                copy_label = (
                    f'Copying track {track_no}'
                    if job.test_and_copy
                    else f'Extracting track {track_no}'
                )
                # Extract is the slow phase — give it most of the track unit so
                # the banner keeps moving; verify/encode keep the last fifth.
                copy_base = 0.35 if job.test_and_copy else 0.0
                copy_span = 0.45 if job.test_and_copy else 0.78
                report(
                    track_no,
                    RipState.RIPPING,
                    index=idx,
                    sub=copy_base,
                    message=f'{copy_label}…',
                    total_units=total_units,
                )

                def _copy_progress(
                    local: float,
                    *,
                    _label=copy_label,
                    _base=copy_base,
                    _span=copy_span,
                ) -> None:
                    local_c = max(0.0, min(1.0, local))
                    report(
                        track_no,
                        RipState.RIPPING,
                        index=idx,
                        sub=_base + _span * local_c,
                        message=f'{_label}…',
                        total_units=total_units,
                    )

                attempt_copy_mode, st = self._rip_track(
                    job.device,
                    track_no,
                    copy_wav,
                    job=job,
                    expected_bytes=expected,
                    min_ratio=job.min_wav_size_ratio,
                    burst_fallback=False if job.test_and_copy else job.burst_fallback,
                    # Archival T&C stays secure-only; Normal/Burst use their own flags.
                    force_mode='secure' if job.test_and_copy else extract_mode,
                    sample_offset=job.sample_offset,
                    on_burst=None
                    if job.test_and_copy
                    else (
                        lambda: report(
                            track_no,
                            RipState.RIPPING,
                            index=idx,
                            sub=0.35,
                            message=(f'Secure extract struggled on track {track_no} — burst mode…'),
                            total_units=total_units,
                        )
                    ),
                    on_extract_progress=_copy_progress,
                    expected_sectors=sectors,
                )
                attempt_stats.merge(st)
                pass_elapsed = time.monotonic() - attempt_t0
                elapsed += pass_elapsed
                report(
                    track_no,
                    RipState.RIPPING,
                    index=idx,
                    sub=0.81,
                    message=f'Checking CRC for track {track_no}…',
                    total_units=total_units,
                )

                def _copy_crc_tick(*, _tn=track_no, _idx=idx) -> None:
                    report(
                        _tn,
                        RipState.RIPPING,
                        index=_idx,
                        sub=0.81,
                        message=f'Checking CRC for track {_tn}…',
                        total_units=total_units,
                        nudge=True,
                    )

                with ProgressPulse(_copy_crc_tick):
                    copy_crc = wav_pcm_crc(copy_wav)
                # Peak filled later from the shared AR/CTDB PCM pass.

                if not job.test_and_copy:
                    matched = True
                    dual_pass = False
                    test_crc = ''
                    test_mode = ''
                    copy_mode = attempt_copy_mode
                    final_mode = attempt_copy_mode
                    track_stats = attempt_stats
                    attempt_log.append(
                        f'pass {attempt + 1}: single extract '
                        f'mode={attempt_copy_mode} CRC={copy_crc.upper()} '
                        f'({pass_elapsed:.1f}s)'
                    )
                    break

                attempt_log.append(
                    f'pass {attempt + 1}: test={attempt_test_mode}/'
                    f'{test_crc.upper() or "?"} copy={attempt_copy_mode}/'
                    f'{copy_crc.upper() or "?"} ({pass_elapsed:.1f}s)'
                )

                if test_crc and copy_crc and test_crc == copy_crc:
                    matched = True
                    test_mode = attempt_test_mode
                    copy_mode = attempt_copy_mode
                    final_mode = attempt_copy_mode
                    track_stats = attempt_stats
                    attempt_log.append(f'pass {attempt + 1}: CRC match {copy_crc.upper()}')
                    break

                matched = False
                last_err = (
                    f'Test/Copy CRC mismatch on track {track_no}: test={test_crc} copy={copy_crc}'
                )
                log.warning('%s', last_err)
                notes.append(last_err)
                track_stats = attempt_stats
                test_mode = attempt_test_mode
                copy_mode = attempt_copy_mode
                final_mode = attempt_copy_mode
                attempt_log.append(
                    f'pass {attempt + 1}: CRC MISMATCH '
                    f'test={test_crc.upper()} copy={copy_crc.upper()}'
                )
                self._unlink_quiet(test_wav, copy_wav)
            except Exception as exc:
                last_err = str(exc)
                log.warning(
                    'Track %s attempt %s failed: %s',
                    track_no,
                    attempt,
                    exc,
                )
                attempt_log.append(f'pass {attempt + 1}: FAILED — {exc}')
                self._unlink_quiet(test_wav, copy_wav)
                if attempt + 1 >= attempts:
                    break

        if job.test_and_copy and not matched and job.burst_fallback and not self._cancelled:
            report(
                track_no,
                RipState.RIPPING,
                index=idx,
                sub=0.40,
                message=(
                    f'Test & copy failed on track {track_no} — recovering that track in burst…'
                ),
                total_units=total_units,
            )
            try:
                recovered = self._extract_track_burst(
                    job=job,
                    track_no=track_no,
                    copy_wav=copy_wav,
                    expected=expected,
                    sectors=sectors,
                    idx=idx,
                    total_units=total_units,
                    report=report,
                )
                elapsed += recovered.elapsed
                track_stats.merge(recovered.stats)
                copy_crc = recovered.copy_crc or copy_crc
                peak = recovered.peak if recovered.peak is not None else peak
                final_mode = 'burst'
                copy_mode = 'burst'
                attempt_log.extend(recovered.attempt_log)
                attempt_log.append(f'per-track burst recovery CRC={copy_crc.upper() or "?"}')
                notes.append(
                    f'Track {track_no}: recovered in burst after secure test & copy mismatch'
                )
            except Exception as exc:
                last_err = str(exc)
                attempt_log.append(f'per-track burst recovery FAILED — {exc}')
                log.warning('Track %s burst recovery failed: %s', track_no, exc)

        return _TrackExtract(
            mode=final_mode,
            test_crc=test_crc if dual_pass else '',
            copy_crc=copy_crc,
            peak=peak,
            elapsed=elapsed,
            stats=track_stats,
            matched=matched,
            copy_wav=copy_wav,
            test_wav=test_wav,
            error=last_err,
            dual_pass=dual_pass,
            test_mode=test_mode,
            copy_mode=copy_mode,
            attempt_log=attempt_log,
            expected_wav_bytes=int(expected or 0),
        )

    def _extract_track_burst(
        self,
        *,
        job: RipJob,
        track_no: int,
        copy_wav: Path,
        expected: int | None,
        sectors: int,
        idx: float,
        total_units: float,
        report: TrackReportFn,
    ) -> _TrackExtract:
        """Single burst (-Z) extract used for per-track CRC recovery."""
        report(
            track_no,
            RipState.RIPPING,
            index=idx,
            sub=0.40,
            message=f'Burst ripping track {track_no}…',
            total_units=total_units,
        )

        def _burst_progress(local: float) -> None:
            local_c = max(0.0, min(1.0, local))
            report(
                track_no,
                RipState.RIPPING,
                index=idx,
                sub=0.40 + 0.15 * local_c,
                message=f'Burst ripping track {track_no}…',
                total_units=total_units,
            )

        t0 = time.monotonic()
        mode, st = self._rip_track(
            job.device,
            track_no,
            copy_wav,
            job=job,
            expected_bytes=expected,
            min_ratio=job.min_wav_size_ratio,
            burst_fallback=False,
            force_mode='burst',
            sample_offset=job.sample_offset,
            on_extract_progress=_burst_progress,
            expected_sectors=sectors,
        )
        elapsed = time.monotonic() - t0
        copy_crc = wav_pcm_crc(copy_wav)
        return _TrackExtract(
            mode=mode,
            test_crc='',  # no dual-pass — do not fake a matching test CRC
            copy_crc=copy_crc,
            peak=None,
            elapsed=elapsed,
            stats=st,
            matched=False,
            copy_wav=copy_wav,
            test_wav=copy_wav,
            dual_pass=False,
            test_mode='',
            copy_mode='burst',
            attempt_log=[f'burst extract CRC={copy_crc.upper()} ({elapsed:.1f}s)'],
            expected_wav_bytes=int(expected or 0),
        )

    @staticmethod
    def _unlink_quiet(*paths: Path) -> None:
        for path in paths:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    @staticmethod
    def _toc_track(disc: DiscInfo | None, track_no: int):
        if disc is None:
            return None
        for t in disc.tracks:
            if t.number == track_no:
                return t
        return None

    @staticmethod
    def _format_extract_mode(
        extracted: _TrackExtract,
        *,
        test_and_copy: bool,
    ) -> str:
        """Human-readable extraction mode for the archival log."""
        if not test_and_copy or not extracted.dual_pass:
            if extracted.mode == 'burst':
                return 'burst (-Z, no test & copy)'
            if extracted.mode == 'normal':
                return 'normal (paranoia, repair allowed)'
            return 'secure (single pass)'
        test_m = extracted.test_mode or 'secure'
        copy_m = extracted.copy_mode or extracted.mode or 'secure'
        if extracted.matched and test_m == 'secure' and copy_m == 'secure':
            return 'secure (test & copy)'
        if extracted.mode == 'burst' or copy_m == 'burst':
            return 'burst (per-track recovery after CRC mismatch)'
        return (
            f'mixed (test={test_m}, copy={copy_m}; test & copy CRC verified)'
            if extracted.matched
            else f'mixed (test={test_m}, copy={copy_m})'
        )

    @staticmethod
    def _apply_extract_to_log(
        log_entry,
        *,
        extracted: _TrackExtract,
        sectors: int,
        expected_bytes: int | None,
        test_and_copy: bool,
    ) -> None:
        if log_entry is None:
            return
        log_entry.extract_mode = type(self)._format_extract_mode(
            extracted,
            test_and_copy=test_and_copy,
        )
        log_entry.extract_seconds = extracted.elapsed
        log_entry.extract_speed_x = extraction_speed_x(sectors, extracted.elapsed)
        # Only record a test CRC when a real dual-pass was performed.
        if extracted.dual_pass:
            log_entry.test_crc = extracted.test_crc
            log_entry.copy_crc = extracted.copy_crc
            log_entry.crc_verified = bool(extracted.matched)
        else:
            log_entry.test_crc = ''
            log_entry.copy_crc = extracted.copy_crc
            log_entry.crc_verified = None
        log_entry.peak_percent = extracted.peak
        log_entry.quality_percent = extracted.stats.quality_percent(sectors)
        log_entry.error_correction_lines = extracted.stats.summary_lines(length_sectors=sectors)
        log_entry.had_errors = bool(
            extracted.stats.had_errors
            or extracted.mode == 'burst'
            or (extracted.dual_pass and not extracted.matched)
        )
        if extracted.stats.had_errors or extracted.mode == 'burst':
            log_entry.status = 'finished with errors'
        else:
            log_entry.status = 'OK'
        log_entry.expected_wav_bytes = int(expected_bytes or extracted.expected_wav_bytes or 0)
        try:
            log_entry.wav_bytes = extracted.copy_wav.stat().st_size
        except OSError:
            pass
        log_entry.attempt_log = list(extracted.attempt_log)
        if extracted.dual_pass and extracted.matched:
            log_entry.notes.append(f'Test and copy CRCs match ({extracted.copy_crc.upper()})')
        elif extracted.mode == 'burst' and extracted.dual_pass:
            log_entry.notes.append(
                'Secure test & copy mismatched; this track recovered in burst (-Z)'
            )
        elif extracted.mode == 'burst':
            log_entry.notes.append('Secure paranoia failed — burst (-Z) used for final audio')
        if (
            log_entry.expected_wav_bytes > 0
            and log_entry.wav_bytes > 0
            and abs(log_entry.wav_bytes - log_entry.expected_wav_bytes) > 44
        ):
            log_entry.notes.append(
                f'WAV size differs from TOC estimate by '
                f'{log_entry.wav_bytes - log_entry.expected_wav_bytes:+d} bytes'
            )

    def _rip_track(
        self,
        device: str,
        track_number: int,
        wav_path: Path,
        *,
        job: RipJob | None = None,
        expected_bytes: int | None = None,
        min_ratio: float = SECURE_MIN_WAV_RATIO,
        burst_fallback: bool = False,
        force_mode: str | None = None,
        sample_offset: int = 0,
        on_burst: Callable[[], None] | None = None,
        on_extract_progress: Callable[[float], None] | None = None,
        expected_sectors: int = 0,
    ) -> tuple[str, ParanoiaStats]:
        """Extract one track. Returns ``(mode, paranoia_stats)``.

        First/last disc tracks use an absolute sector span widened into
        lead-in/out when the drive can overread (then trimmed to TOC length).
        Middle tracks keep cdparanoia track-number mode (``-O`` can borrow
        from neighbors).

        *force_mode*:
          - ``None`` — archival secure first, optional ``-Z`` on extract failure
          - ``'burst'`` — burst only (``-Z``)
          - ``'secure'`` — archival secure only (``-X``, no burst)
          - ``'normal'`` — paranoia with repair (no ``-X``)
        """
        primary_mode = (
            force_mode if force_mode in ('secure', 'normal', 'burst') else 'secure'
        )
        edge = _job_per_track_overread_span(job, track_number)
        if edge is not None:
            read_start, read_end, toc_expected = edge
            progress_bytes = 44 + max(0, read_end - read_start) * 2352
            assert_expected = expected_bytes if expected_bytes is not None else toc_expected
            mode, stats = self._rip_span(
                device,
                read_start,
                read_end,
                wav_path,
                expected_bytes=assert_expected,
                progress_expected_bytes=progress_bytes,
                min_ratio=min_ratio,
                burst_fallback=burst_fallback and primary_mode != 'secure',
                force_mode=primary_mode,
                sample_offset=sample_offset,
                on_burst=on_burst,
                on_extract_progress=on_extract_progress,
            )
            if assert_expected:
                trim_wav_to_expected(wav_path, int(assert_expected))
            return mode, stats

        primary_timeout = _extract_timeout(expected_bytes, mode=primary_mode)
        burst_timeout = _extract_timeout(expected_bytes, mode='burst')
        expected_frames = max(0, int(expected_sectors)) * FRAMES_PER_SECTOR

        def run_primary() -> ParanoiaStats:
            return self._run_cdparanoia(
                device,
                track_number,
                wav_path,
                mode=primary_mode if primary_mode != 'burst' else 'secure',
                timeout=primary_timeout,
                allow_nonzero=True,
                sample_offset=sample_offset,
                on_extract_progress=on_extract_progress,
                expected_frames=expected_frames,
                expected_bytes=expected_bytes,
            )

        def run_burst() -> ParanoiaStats:
            return self._run_cdparanoia(
                device,
                track_number,
                wav_path,
                mode='burst',
                timeout=burst_timeout,
                allow_nonzero=True,
                sample_offset=sample_offset,
                on_extract_progress=on_extract_progress,
                expected_frames=expected_frames,
                expected_bytes=expected_bytes,
            )

        if primary_mode == 'burst':
            if on_burst is not None:
                on_burst()
            try:
                if wav_path.exists():
                    wav_path.unlink()
            except OSError:
                pass
            burst_stats = run_burst()
            self._assert_wav_ok(
                wav_path,
                track_number,
                expected_bytes=expected_bytes,
                min_ratio=max(0.5, min_ratio * 0.85),
                label='burst',
            )
            return 'burst', burst_stats

        return self._extract_with_burst_fallback(
            wav_path,
            track_number=track_number,
            expected_bytes=expected_bytes,
            min_ratio=min_ratio,
            burst_fallback=burst_fallback and primary_mode != 'secure',
            primary_mode=primary_mode,
            run_primary=run_primary,
            run_burst=run_burst,
            on_burst=on_burst,
            what=f'track {track_number}',
        )

    def _probe_job_overread(
        self,
        job: RipJob,
        on_progress: ProgressCallback | None,
    ) -> None:
        """Fill ``job.overread`` via MMC READ CD on the rip worker thread."""
        if on_progress is not None:
            on_progress(
                RipProgress(
                    track_number=0,
                    state=RipState.PREPARING,
                    fraction=0.01,
                    message='Probing drive overread…',
                )
            )
        disc = job.disc_info
        leadout = int(getattr(disc, 'leadout_lba', 0) or 0) if disc else 0
        if leadout <= 0 and disc is not None and disc.tracks:
            last = disc.tracks[-1]
            leadout = int(last.start_sector) + int(last.length_sectors)
        try:
            job.overread = probe_overread(job.device, leadout_lba=leadout)
        except Exception as exc:
            log.debug('Overread probe failed: %s', exc)
            job.overread = OverreadCapability()
        label = job.overread.log_label
        if label == 'Yes':
            detail = 'lead-in and lead-out'
        elif label == 'No':
            detail = 'silence fill at offset edges'
        else:
            detail = 'partial'
        if on_progress is not None:
            on_progress(
                RipProgress(
                    track_number=0,
                    state=RipState.PREPARING,
                    fraction=0.02,
                    message=f'Overread: {label} · {detail}',
                )
            )

    def _rip_span(
        self,
        device: str,
        start_sector: int,
        end_sector: int,
        wav_path: Path,
        *,
        expected_bytes: int | None = None,
        progress_expected_bytes: int | None = None,
        min_ratio: float = SECURE_MIN_WAV_RATIO,
        burst_fallback: bool = False,
        force_mode: str | None = None,
        sample_offset: int = 0,
        on_burst: Callable[[], None] | None = None,
        on_extract_progress: Callable[[float], None] | None = None,
        timeout_secure: int | None = None,
        timeout_burst: int | None = None,
    ) -> tuple[str, ParanoiaStats]:
        """Extract a sector span (Copy Image / continuous audio).

        *expected_bytes* gates WAV completeness (TOC size).
        *progress_expected_bytes* should match the actual read span when
        overread widens the extract (defaults to *expected_bytes*).
        """
        primary_mode = (
            force_mode if force_mode in ('secure', 'normal', 'burst') else 'secure'
        )
        if timeout_secure is None:
            timeout_secure = _extract_timeout(expected_bytes, mode=primary_mode)
        if timeout_burst is None:
            timeout_burst = _extract_timeout(expected_bytes, mode='burst')
        expected_frames = max(0, int(end_sector) - int(start_sector)) * FRAMES_PER_SECTOR
        progress_bytes = (
            expected_bytes if progress_expected_bytes is None else progress_expected_bytes
        )

        def run_primary() -> ParanoiaStats:
            return self._run_cdparanoia_span(
                device,
                start_sector,
                end_sector,
                wav_path,
                mode=primary_mode if primary_mode != 'burst' else 'secure',
                timeout=timeout_secure,
                allow_nonzero=True,
                sample_offset=sample_offset,
                on_extract_progress=on_extract_progress,
                expected_frames=expected_frames,
                expected_bytes=progress_bytes,
            )

        def run_burst() -> ParanoiaStats:
            return self._run_cdparanoia_span(
                device,
                start_sector,
                end_sector,
                wav_path,
                mode='burst',
                timeout=timeout_burst,
                allow_nonzero=True,
                sample_offset=sample_offset,
                on_extract_progress=on_extract_progress,
                expected_frames=expected_frames,
                expected_bytes=progress_bytes,
            )

        if primary_mode == 'burst':
            if on_burst is not None:
                on_burst()
            try:
                if wav_path.exists():
                    wav_path.unlink()
            except OSError:
                pass
            burst_stats = run_burst()
            self._assert_wav_ok(
                wav_path,
                0,
                expected_bytes=expected_bytes,
                min_ratio=max(0.5, min_ratio * 0.85),
                label='burst',
            )
            return 'burst', burst_stats

        return self._extract_with_burst_fallback(
            wav_path,
            track_number=0,
            expected_bytes=expected_bytes,
            min_ratio=min_ratio,
            burst_fallback=burst_fallback and primary_mode != 'secure',
            primary_mode=primary_mode,
            run_primary=run_primary,
            run_burst=run_burst,
            on_burst=on_burst,
            what=f'span [{start_sector}, {end_sector})',
        )

    def _extract_with_burst_fallback(
        self,
        wav_path: Path,
        *,
        track_number: int,
        expected_bytes: int | None,
        min_ratio: float,
        burst_fallback: bool,
        primary_mode: str,
        run_primary: Callable[[], ParanoiaStats],
        run_burst: Callable[[], ParanoiaStats],
        on_burst: Callable[[], None] | None,
        what: str,
    ) -> tuple[str, ParanoiaStats]:
        """Primary extract first; on incomplete/failure, optional ``-Z`` burst."""
        primary_err: str | None = None
        primary_stats = ParanoiaStats()
        mode_label = primary_mode if primary_mode in ('secure', 'normal') else 'secure'

        # Prefer a complete primary WAV even when cdparanoia exits non-zero
        # (e.g. -X after exhausted retries — file may still be full length).
        try:
            primary_stats = run_primary()
            self._assert_wav_ok(
                wav_path,
                track_number,
                expected_bytes=expected_bytes,
                min_ratio=min_ratio,
            )
            # -X abort / hard skip with incomplete recovery: treat as failure
            # even when the file is large enough to pass the size check.
            if self._secure_pass_needs_burst(primary_stats, wav_path, expected_bytes):
                raise RuntimeError(
                    f'{mode_label.capitalize()} pass aborted or incomplete after skips '
                    f'(exit {primary_stats.exit_code})'
                )
            return mode_label, primary_stats
        except Exception as exc:
            if self._cancelled:
                raise
            primary_err = str(exc)
            log.warning('%s rip failed for %s: %s', mode_label.capitalize(), what, primary_err)
            # Keep a full-size primary file if we already have one *and*
            # paranoia did not report an abort-with-skips situation.
            if not self._secure_pass_needs_burst(primary_stats, wav_path, expected_bytes):
                try:
                    self._assert_wav_ok(
                        wav_path,
                        track_number,
                        expected_bytes=expected_bytes,
                        min_ratio=min_ratio,
                    )
                    log.info(
                        '%s: accepting %s WAV despite cdparanoia error',
                        what,
                        mode_label,
                    )
                    return mode_label, primary_stats
                except Exception:
                    pass

        if not burst_fallback:
            raise RuntimeError(f'{mode_label.capitalize()} rip failed for {what}: {primary_err}')

        if on_burst is not None:
            on_burst()

        try:
            if wav_path.exists():
                wav_path.unlink()
        except OSError:
            pass

        try:
            burst_stats = run_burst()
            self._assert_wav_ok(
                wav_path,
                track_number,
                expected_bytes=expected_bytes,
                min_ratio=max(0.5, min_ratio * 0.85),
                label='burst',
            )
        except Exception as exc:
            raise RuntimeError(
                f'{mode_label.capitalize()} and burst rip both failed for {what}. '
                f'{mode_label.capitalize()}: {primary_err}; burst: {exc}'
            ) from exc

        log.info('%s recovered via burst mode (-Z)', what)
        combined = primary_stats.merge(burst_stats) if primary_stats.reads else burst_stats
        return 'burst', combined

    @staticmethod
    def _secure_pass_needs_burst(
        stats: ParanoiaStats,
        wav_path: Path,
        expected_bytes: int | None,
    ) -> bool:
        """True when a size-OK secure pass still should try burst.

        cdparanoia with ``-X`` can leave a large but truncated WAV after skips;
        prefer burst recovery when the process aborted with hard errors.
        """
        if stats.exit_code in (None, 0):
            return False
        hard = stats.skips > 0 or stats.readerrs > 0 or stats.scratch > 0
        # Aborted mid-track (never emitted finished) with hard errors.
        if hard and not stats.finished:
            return True
        if expected_bytes and expected_bytes > 1000 and wav_path.is_file():
            try:
                size = wav_path.stat().st_size
            except OSError:
                return True
            # Near-full files after an abort that still pass min_ratio (e.g. 98%).
            if size < int(expected_bytes * 0.995):
                return True
        return False

