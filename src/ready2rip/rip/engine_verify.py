# SPDX-License-Identifier: GPL-3.0-or-later
"""VerifyMixin methods for :class:`~ready2rip.rip.engine.RipEngine`."""

from __future__ import annotations

from ready2rip.rip._engine_shared import *  # noqa: F403

# NOTE: This mixin is composed onto RipEngine. Method bodies expect the full
# RipEngine attribute/helper surface (imports resolved in engine.py).


class VerifyMixin:
    @staticmethod
    def _record_image_verify_load_failure(
        disc: DiscInfo,
        *,
        message: str,
        extract_mode: str,
        ar_verifier: AccurateRipVerifier | None,
        ctdb_verifier: CtdbVerifier | None,
        ar_results: list[AccurateRipResult],
        ctdb_results: list[CtdbResult],
        rip_log: RipLog | None,
    ) -> None:
        """Append per-track ERROR results when Copy Image PCM cannot be loaded."""
        mode_label = f'{extract_mode} (copy image)'
        for track in disc.tracks:
            entry = None
            if rip_log is not None:
                entry = rip_log.begin_track(
                    track.number,
                    title=title_for_track(rip_log.album, track.number),
                    length_label=track.duration_label,
                    start_sector=track.start_sector,
                    length_sectors=track.length_sectors,
                )
                entry.extract_mode = mode_label
                entry.status = 'verify failed'
                entry.had_errors = True
            if ar_verifier is not None:
                ar = AccurateRipResult(
                    track.number,
                    AccurateRipConfidence.ERROR,
                    message=message,
                )
                ar_results.append(ar)
                if entry is not None:
                    entry.accuraterip = ar
            if ctdb_verifier is not None:
                ctdb = CtdbResult(
                    track.number,
                    CtdbConfidence.ERROR,
                    message=message,
                )
                ctdb_results.append(ctdb)
                if entry is not None:
                    entry.ctdb = ctdb

    def _verify_image_databases(
        self,
        wav_path: Path,
        disc: DiscInfo,
        *,
        file_start: int,
        extract_mode: str,
        image_stats: ParanoiaStats | None = None,
        ar_verifier: AccurateRipVerifier | None,
        ctdb_verifier: CtdbVerifier | None,
        ar_results: list[AccurateRipResult],
        ctdb_results: list[CtdbResult],
        notes: list[str],
        rip_log: RipLog | None,
        report: Callable[[RipState, float, str], None],
    ) -> None:
        """TOC-split a continuous image WAV and run AccurateRip + CTDB per track."""
        if ar_verifier is None and ctdb_verifier is None:
            return
        if not disc.tracks:
            return

        report(RipState.VERIFYING, 0.70, 'Opening disc image for verification…')

        try:
            image_view_cm = open_cdda_wav_mmap(wav_path)
        except Exception as exc:
            msg = f'Image PCM load failed: {exc}'
            log.warning('%s', msg)
            notes.append(msg)
            # Fail closed: empty results would otherwise skip archival checks.
            self._record_image_verify_load_failure(
                disc,
                message=msg,
                extract_mode=extract_mode,
                ar_verifier=ar_verifier,
                ctdb_verifier=ctdb_verifier,
                ar_results=ar_results,
                ctdb_results=ctdb_results,
                rip_log=rip_log,
            )
            return

        mode_label = f'{extract_mode} (copy image)'
        image_quality: float | None = None
        image_had_errors = False
        intentional_burst = bool(
            rip_log is not None
            and (
                rip_log.burst_mode
                or (rip_log.rip_mode or '').strip().lower() == 'burst'
            )
        )
        if image_stats is not None:
            total_secs = sum(int(t.length_sectors) for t in disc.tracks)
            image_quality = image_stats.quality_percent(total_secs)
            recovered_burst = extract_mode == 'burst' and not intentional_burst
            image_had_errors = bool(image_stats.had_errors) or recovered_burst
        n = max(1, len(disc.tracks))
        with image_view_cm as image_view:
            for i, track in enumerate(disc.tracks):
                if self._cancelled:
                    break
                frac = 0.70 + 0.05 * ((i + 1) / n)
                report(
                    RipState.VERIFYING,
                    frac,
                    f'Verifying image track {track.number}…',
                )
                expected = max(0, int(track.length_sectors)) * 2352
                pcm = image_track_pcm_from_view(
                    image_view,
                    start_sector=track.start_sector,
                    length_sectors=track.length_sectors,
                    file_start=file_start,
                )
                log_entry: TrackLogEntry | None = None
                if rip_log is not None:
                    log_entry = rip_log.begin_track(
                        track.number,
                        title=title_for_track(rip_log.album, track.number),
                        length_label=track.duration_label,
                        start_sector=track.start_sector,
                        length_sectors=track.length_sectors,
                    )
                    log_entry.extract_mode = mode_label
                    log_entry.quality_percent = image_quality
                    log_entry.had_errors = image_had_errors
                if not pcm:
                    msg = (
                        f'Track {track.number}: empty PCM slice in image '
                        f'(start={track.start_sector}, file_start={file_start})'
                    )
                    notes.append(msg)
                    if log_entry is not None:
                        log_entry.status = 'ERROR'
                        log_entry.had_errors = True
                        log_entry.notes.append(msg)
                    if ar_verifier is not None:
                        ar = AccurateRipResult(
                            track.number,
                            AccurateRipConfidence.ERROR,
                            message=msg,
                        )
                        ar_results.append(ar)
                        if log_entry is not None:
                            log_entry.accuraterip = ar
                    if ctdb_verifier is not None:
                        ctdb = CtdbResult(
                            track.number,
                            CtdbConfidence.ERROR,
                            message=msg,
                        )
                        ctdb_results.append(ctdb)
                        if log_entry is not None:
                            log_entry.ctdb = ctdb
                    continue
                if expected and len(pcm) < expected:
                    msg = (
                        f'Track {track.number}: short PCM slice in image '
                        f'({len(pcm)} < {expected} bytes) — verification may fail'
                    )
                    notes.append(msg)
                    if log_entry is not None:
                        log_entry.notes.append(msg)
                        log_entry.had_errors = True

                if log_entry is not None:
                    _crc, peak = analyze_pcm_for_log(pcm)
                    if peak is not None:
                        log_entry.peak_percent = peak
                    if _crc:
                        log_entry.copy_crc = _crc

                if ar_verifier is not None:
                    ar = ar_verifier.verify_pcm(pcm, track.number)
                    ar_results.append(ar)
                    notes.append(f'Track {track.number} AR: {ar.message}')
                    if log_entry is not None:
                        log_entry.accuraterip = ar

                if ctdb_verifier is not None:
                    ctdb = ctdb_verifier.verify_pcm(pcm, track.number)
                    ctdb_results.append(ctdb)
                    notes.append(f'Track {track.number} CTDB: {ctdb.message}')
                    if log_entry is not None:
                        log_entry.ctdb = ctdb

    def _reverify_existing_track(
        self,
        *,
        job: RipJob,
        track_no: int,
        tmp_path: Path,
        ar_verifier: AccurateRipVerifier | None,
        ctdb_verifier: CtdbVerifier | None,
        ar_results: list[AccurateRipResult],
        ctdb_results: list[CtdbResult],
        log_entry: TrackLogEntry | None,
        idx: float,
        total_units: float,
        report: TrackReportFn,
        outputs: list[Path],
        track_output_pairs: list[tuple[int, Path]],
        notes: list[str],
    ) -> bool:
        """Decode an existing primary encode and run AR/CTDB when possible.

        Returns True when the track was re-verified (or verify is off).
        """
        probe = replace(job, existing_files='overwrite')
        existing = self._destinations_for_track(probe, track_no)
        if not existing:
            return not (job.verify_accuraterip or job.verify_ctdb)
        primary = next((d for d in existing if d.primary), existing[0])
        if not primary.out_path.is_file():
            return False
        outputs.append(primary.out_path)
        if primary.primary:
            track_output_pairs.append((track_no, primary.out_path))
        if log_entry is not None:
            log_entry.output_path = primary.out_path
            log_entry.filename = primary.out_path.name

        need_verify = ar_verifier is not None or ctdb_verifier is not None
        if not need_verify:
            return True

        wav = tmp_path / f'track{track_no:02d}_existing.wav'
        try:
            if not self._decode_existing_to_wav(primary.out_path, wav):
                notes.append(
                    f'Could not decode existing {primary.out_path.name} '
                    f'for track {track_no} re-verify'
                )
                return False
            self._verify_track_databases(
                wav,
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
            return True
        except Exception as exc:
            log.warning('Re-verify existing track %s failed: %s', track_no, exc)
            notes.append(f'Re-verify failed for existing track {track_no}: {exc}')
            return False
        finally:
            self._unlink_quiet(wav)

    @staticmethod
    def _m4a_is_alac(path: Path) -> bool:
        """True when *path* is ALAC (lossless); False for AAC / unknown."""
        try:
            from mutagen.mp4 import MP4

            info = MP4(path).info
            for entry in getattr(info, 'codec_list', None) or ():
                name = str(entry).lower()
                if 'alac' in name:
                    return True
                if 'aac' in name or name in {'mp4a', 'mp4a-40-2'}:
                    return False
            codec = str(getattr(info, 'codec', '') or '').lower()
            if 'alac' in codec:
                return True
            if 'aac' in codec:
                return False
            return False
        except Exception as exc:
            log.debug('ALAC probe failed for %s: %s', path, exc)
            return False

    @staticmethod
    def _decode_existing_to_wav(src: Path, dest: Path) -> bool:
        """Decode a lossless (or WAV) file to 16-bit stereo 44.1 kHz WAV."""
        suffix = src.suffix.lower()
        if suffix == '.wav':
            try:
                shutil.copy2(src, dest)
                return dest.is_file() and dest.stat().st_size > 1000
            except OSError:
                return False
        if suffix == '.flac':
            flac = shutil.which('flac')
            if not flac:
                return False
            cmd = [flac, '-d', '-f', '-o', str(dest), str(src)]
        elif suffix in {'.aiff', '.aif', '.wv'}:
            ffmpeg = shutil.which('ffmpeg')
            if not ffmpeg:
                return False
            cmd = [
                ffmpeg,
                '-y',
                '-i',
                str(src),
                '-acodec',
                'pcm_s16le',
                '-ar',
                '44100',
                '-ac',
                '2',
                str(dest),
            ]
        elif suffix == '.m4a':
            # Only ALAC is archival; AAC .m4a must not count as re-verified.
            if not VerifyMixin._m4a_is_alac(src):
                return False
            ffmpeg = shutil.which('ffmpeg')
            if not ffmpeg:
                return False
            cmd = [
                ffmpeg,
                '-y',
                '-i',
                str(src),
                '-acodec',
                'pcm_s16le',
                '-ar',
                '44100',
                '-ac',
                '2',
                str(dest),
            ]
        else:
            # Lossy companions (mp3/opus/aac) — cannot re-verify for archival.
            return False
        try:
            completed = subprocess.run(
                cmd,
                check=False,
                capture_output=True,
                text=True,
                timeout=600,
                env=subprocess_env_for(cmd),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            log.debug('Decode existing failed (%s): %s', src, exc)
            return False
        if completed.returncode != 0 or not dest.is_file() or dest.stat().st_size < 1000:
            detail = (completed.stderr or completed.stdout or '').strip()
            log.debug('Decode existing failed (%s): %s', src, detail[:200])
            return False
        return True

    def _verify_track_databases(
        self,
        wav_path: Path,
        track_no: int,
        *,
        ar_verifier: AccurateRipVerifier | None,
        ctdb_verifier: CtdbVerifier | None,
        ar_results: list[AccurateRipResult],
        ctdb_results: list[CtdbResult],
        log_entry: TrackLogEntry | None,
        idx: float,
        total_units: float,
        report: TrackReportFn,
    ) -> None:
        """One PCM load for peak/CRC log fields + AccurateRip + CTDB."""
        need_peak = log_entry is not None and log_entry.peak_percent is None
        if ar_verifier is None and ctdb_verifier is None and not need_peak:
            return

        report(
            track_no,
            RipState.VERIFYING,
            index=idx,
            sub=0.82,
            message=f'Verifying track {track_no}…',
            total_units=total_units,
        )
        pcm: bytes | None = None
        try:
            pcm = load_cdda_pcm(wav_path)
        except Exception as exc:
            log.warning('PCM load failed for track %s: %s', track_no, exc)

        if pcm is not None and log_entry is not None:
            _crc, peak = analyze_pcm_for_log(pcm)
            if peak is not None:
                log_entry.peak_percent = peak

        if ar_verifier is not None:
            if pcm is not None:
                ar = ar_verifier.verify_pcm(pcm, track_no)
            else:
                ar = ar_verifier.verify_wav(wav_path, track_no)
            ar_results.append(ar)
            if log_entry is not None:
                log_entry.accuraterip = ar
            report(
                track_no,
                RipState.VERIFYING,
                index=idx,
                sub=0.85,
                message=ar.message,
                total_units=total_units,
            )

        if ctdb_verifier is not None:
            report(
                track_no,
                RipState.VERIFYING,
                index=idx,
                sub=0.86,
                message=f'CTDB check track {track_no}…',
                total_units=total_units,
            )
            if pcm is not None:
                ctdb = ctdb_verifier.verify_pcm(pcm, track_no)
            else:
                ctdb = ctdb_verifier.verify_wav(wav_path, track_no)
            ctdb_results.append(ctdb)
            if log_entry is not None:
                log_entry.ctdb = ctdb
            report(
                track_no,
                RipState.VERIFYING,
                index=idx,
                sub=0.88,
                message=ctdb.message,
                total_units=total_units,
            )

