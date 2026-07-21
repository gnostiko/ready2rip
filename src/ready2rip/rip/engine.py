# SPDX-License-Identifier: GPL-3.0-or-later
"""Secure test & copy extraction + encode + tag + AccurateRip pipeline.

Workflow (whipper / cyanrip inspired):
  1. Create album folder; save full-size cover art; prepare embed-sized art
  2. Detect drive audio cache; enable cache defeat between test/copy
  3. Detect and rip non-silent Hidden Track One Audio (HTOA)
  4. Per track: test extract → CRC, defeat cache, copy extract → CRC, match
  5. AccurateRip verify, encode, tag, embed artwork
  6. Track + album ReplayGain on the finished set
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Callable

from ready2rip.accuraterip import AccurateRipConfidence, AccurateRipResult, AccurateRipVerifier
from ready2rip.artwork.fetch import ArtworkFetcher, ArtworkImage
from ready2rip.disc.cache import DriveCacheResult, flush_drive_cache
from ready2rip.disc.htoa import (
    detect_htoa,
    extract_htoa,
    htoa_display_title,
    is_digitally_silent,
)
from ready2rip.disc.probe import DiscInfo
from ready2rip.metadata.providers import AlbumMetadata
from ready2rip.paths import build_output_paths
from ready2rip.replaygain import apply_replaygain
from ready2rip.rip.cue import (
    cue_file_type_for_extension,
    image_basename,
    multi_file_cue_basename,
    write_cue_sheet,
    write_multi_file_cue_sheet,
)
from ready2rip.rip.paranoia_stats import ParanoiaStats, parse_paranoia_stderr
from ready2rip.rip.riplog import (
    RipLog,
    analyze_wav_for_log,
    extraction_speed_x,
    title_for_track,
)
from ready2rip.tags.writer import TagWriter
from ready2rip.util import find_cdparanoia, validate_device_path

log = logging.getLogger(__name__)

# EAC-like secure defaults for cdparanoia / libcdio-paranoia.
# never-skip=N: keep re-reading imperfect data until N stuck retries.
# -X: abort rather than silently accept a skip after never-skip is exhausted.
SECURE_NEVER_SKIP = 200


class RipState(Enum):
    PREPARING = auto()
    RIPPING = auto()
    VERIFYING = auto()
    ENCODING = auto()
    TAGGING = auto()
    REPLAYGAIN = auto()
    DONE = auto()
    FAILED = auto()


@dataclass
class RipProgress:
    track_number: int
    state: RipState
    fraction: float = 0.0  # overall 0..1
    message: str = ''
    current_path: Path | None = None


@dataclass
class RipResult:
    success: bool
    output_files: list[Path] = field(default_factory=list)
    album_dir: Path | None = None
    error: str | None = None
    cancelled: bool = False
    accuraterip: list[AccurateRipResult] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    burst_tracks: list[int] = field(default_factory=list)
    log_path: Path | None = None
    cover_path: Path | None = None
    htoa_ripped: bool = False
    cache_result: DriveCacheResult | None = None
    test_copy_mismatches: list[int] = field(default_factory=list)


@dataclass
class RipJob:
    """Description of a full-disc or multi-track rip."""

    device: str
    track_numbers: list[int]
    output_directory: Path
    encode_format: str = 'flac'  # flac | mp3 | opus | wav
    flac_compression: int = 5
    mp3_bitrate: int = 320
    opus_bitrate: int = 160
    apply_replaygain: bool = True
    embed_artwork: bool = True
    # Embed-sized image written into each audio file.
    artwork: ArtworkImage | None = None
    # Full-resolution image saved as cover.jpg/png in the album folder.
    folder_artwork: ArtworkImage | None = None
    album: AlbumMetadata | None = None
    filename_template: str = '{track:02d} - {title}'
    album_folder_template: str = '{album_artist}/{album}'
    save_cover_file: bool = True
    verify_accuraterip: bool = True
    sample_offset: int = 0
    disc_info: DiscInfo | None = None
    # Total tracks on the disc (for AR first/last sample skip), not just selected.
    disc_track_count: int = 0
    # If secure (paranoia) extraction fails, retry with cdparanoia -Z burst mode.
    burst_fallback: bool = True
    # Expected minimum WAV size as a fraction of ideal CDDA size (EAC-like: near complete).
    min_wav_size_ratio: float = 0.98
    # Write an EAC-style detailed status log next to the ripped files.
    write_rip_log: bool = True
    # Write EAC-style multi-file (or image) CUE sheet after a secure rip.
    write_cue_file: bool = True
    # Double-rip each track and require matching CRCs (test & copy).
    test_and_copy: bool = True
    # Max extra full test+copy cycles after a CRC mismatch.
    test_copy_max_retries: int = 3
    # Persisted from Drive setup (None = unknown / not measured).
    drive_caches_audio: bool | None = None
    drive_cache_message: str = ''
    drive_accurate_stream: bool | None = None
    drive_accurate_stream_message: str = ''
    drive_c2_pointers: bool | None = None
    drive_c2_message: str = ''
    # Seek/read elsewhere between test and copy when cache is present (or always).
    defeat_audio_cache: bool = True
    # EAC-style: handle extended track-1 pregap as HTOA (track 00); never
    # prepend that pregap onto track 1. Standard 2s pause is ignored.
    rip_htoa: bool = True
    # EAC "Copy Image": one continuous image (instead of per-track files).
    # Pair with write_cue_file for a matching image CUE sheet.
    copy_image: bool = False
    # Longest edge for embed when preparing from folder art (0 = no downscale).
    artwork_max_size: int = 600


ProgressCallback = Callable[[RipProgress], None]


class RipEngine:
    """Coordinates test&copy, HTOA, AccurateRip, encode, tag, ReplayGain, art."""

    EXTENSIONS = {
        'flac': '.flac',
        'mp3': '.mp3',
        'opus': '.opus',
        'wav': '.wav',
    }

    def __init__(self) -> None:
        self._cancelled = False
        self._proc: subprocess.Popen | None = None
        self._tags = TagWriter()

    def cancel(self) -> None:
        self._cancelled = True
        proc = self._proc
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass

    def run(self, job: RipJob, on_progress: ProgressCallback | None = None) -> RipResult:
        self._cancelled = False
        tracks = sorted(n for n in job.track_numbers if n > 0)
        if not tracks and not job.rip_htoa and not job.copy_image:
            return RipResult(success=False, error='No tracks selected')

        if find_cdparanoia() is None:
            return RipResult(
                success=False,
                error='cdparanoia not found (install cdparanoia or libcdio-paranoia)',
            )

        try:
            job.device = validate_device_path(job.device)
        except ValueError as exc:
            return RipResult(success=False, error=str(exc))

        fmt = (job.encode_format or 'flac').lower()
        if fmt not in self.EXTENSIONS:
            return RipResult(success=False, error=f'Unknown format: {fmt}')

        # CD images + CUE need a lossless/container format (EAC: WAV/FLAC).
        if job.copy_image and fmt not in ('flac', 'wav'):
            log.info('Copy Image mode: using FLAC instead of %s', fmt)
            fmt = 'flac'
            job.encode_format = 'flac'

        missing = self._missing_encoder(fmt)
        if missing:
            return RipResult(success=False, error=missing)

        base = job.output_directory.expanduser().resolve()
        try:
            base.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return RipResult(success=False, error=f'Cannot create output folder: {exc}')

        if job.copy_image:
            return self._run_copy_image(job, base, fmt, on_progress)

        # Work units: optional HTOA (track 0) + selected audio tracks.
        n_audio = max(1, len(tracks))
        outputs: list[Path] = []
        ar_results: list[AccurateRipResult] = []
        notes: list[str] = []
        burst_tracks: list[int] = []
        test_copy_mismatches: list[int] = []
        album_dir: Path | None = None
        log_path: Path | None = None
        cover_path: Path | None = None
        htoa_ripped = False
        cache_result: DriveCacheResult | None = None
        ext = self.EXTENSIONS[fmt]
        expected_sizes = _expected_wav_sizes(job.disc_info)

        rip_log: RipLog | None = None
        if job.write_rip_log:
            rip_log = RipLog()
            rip_log.configure_from_job(job)

        # --- Album folder + artwork *before* any disc reads for tracks ---
        first_for_paths = tracks[0] if tracks else 1
        album_dir, _ = build_output_paths(
            base_dir=base,
            album_folder_template=job.album_folder_template,
            filename_template=job.filename_template,
            album=job.album,
            track_number=first_for_paths,
            extension=ext,
        )
        album_dir.mkdir(parents=True, exist_ok=True)

        def report(
            track_number: int,
            state: RipState,
            *,
            index: float = 0.0,
            sub: float = 0.0,
            message: str = '',
            path: Path | None = None,
            total_units: float | None = None,
        ) -> None:
            if on_progress is None:
                return
            units = total_units if total_units is not None else float(n_audio)
            units = max(1.0, units)
            overall = (index + max(0.0, min(1.0, sub))) / units
            on_progress(
                RipProgress(
                    track_number=track_number,
                    state=state,
                    fraction=max(0.0, min(1.0, overall)),
                    message=message,
                    current_path=path,
                )
            )

        report(
            first_for_paths,
            RipState.PREPARING,
            index=0.0,
            sub=0.0,
            message='Preparing album folder and artwork…',
            path=album_dir,
        )

        embed_art, folder_art, cover_path, art_notes = _prepare_artwork(
            job, album_dir
        )
        notes.extend(art_notes)
        if cover_path is not None:
            notes.append(f'Cover art saved: {cover_path}')
            if rip_log is not None:
                rip_log.notes.append(f'Cover art saved before rip: {cover_path}')

        # AccurateRip setup. Sample offset is applied at extract time via
        # cdparanoia -O (EAC-style), so AR hashes the offset-corrected WAV as-is.
        ar_verifier: AccurateRipVerifier | None = None
        if job.verify_accuraterip and job.disc_info is not None:
            ar_verifier = AccurateRipVerifier(sample_offset=0)
            status = ar_verifier.prepare(job.disc_info)
            notes.append(status)
            if job.sample_offset:
                notes.append(
                    f'Read offset {job.sample_offset} samples applied during extraction (-O)'
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

        # Drive features: use results persisted from Drive setup.
        defeat_cache = job.defeat_audio_cache
        if job.drive_accurate_stream is not None:
            amsg = job.drive_accurate_stream_message or (
                'Accurate Stream: yes'
                if job.drive_accurate_stream
                else 'Accurate Stream: no'
            )
            notes.append(amsg)
            if rip_log is not None:
                rip_log.accurate_stream = job.drive_accurate_stream
                rip_log.accurate_stream_message = amsg
        if job.drive_c2_pointers is not None:
            cmsg = job.drive_c2_message or (
                'C2 Error pointers: yes'
                if job.drive_c2_pointers
                else 'C2 Error pointers: no'
            )
            notes.append(cmsg)
            if rip_log is not None:
                rip_log.c2_pointers = job.drive_c2_pointers
                rip_log.c2_message = cmsg
        if job.drive_caches_audio is not None:
            msg = job.drive_cache_message or (
                'Drive caches audio (from Drive setup)'
                if job.drive_caches_audio
                else 'No clear audio cache (from Drive setup)'
            )
            cache_result = DriveCacheResult(
                caches=job.drive_caches_audio,
                message=msg,
            )
            notes.append(msg)
            if rip_log is not None:
                rip_log.cache_message = msg
                rip_log.drive_caches = job.drive_caches_audio
            if job.drive_caches_audio:
                defeat_cache = True
                notes.append('Cache defeat enabled between test and copy')
        elif defeat_cache:
            notes.append(
                'Drive cache not measured — defeating cache between test & copy anyway'
            )
            if rip_log is not None:
                rip_log.cache_message = 'Not measured (Drive setup)'
                rip_log.drive_caches = None

        # Count HTOA as an extra work unit for progress when present.
        htoa_info = detect_htoa(job.disc_info) if job.rip_htoa else None
        total_units = float(len(tracks) + (1 if htoa_info else 0)) or 1.0
        unit_index = 0.0
        htoa_path: Path | None = None
        # (track_number, path) for multi-file CUE generation after encode.
        track_output_pairs: list[tuple[int, Path]] = []

        try:
            with tempfile.TemporaryDirectory(prefix='ready2rip-') as tmp:
                tmp_path = Path(tmp)

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
                    htoa_ok, htoa_path, htoa_notes = self._rip_htoa_unit(
                        job=job,
                        htoa_info=htoa_info,
                        tmp_path=tmp_path,
                        album_dir=album_dir,
                        fmt=fmt,
                        ext=ext,
                        embed_art=embed_art,
                        defeat_cache=defeat_cache,
                        rip_log=rip_log,
                        report=lambda state, sub, msg, path=None: report(
                            0,
                            state,
                            index=unit_index,
                            sub=sub,
                            message=msg,
                            path=path,
                            total_units=total_units,
                        ),
                    )
                    notes.extend(htoa_notes)
                    if htoa_ok and htoa_path is not None:
                        outputs.append(htoa_path)
                        htoa_ripped = True
                    unit_index += 1.0

                # --- Regular tracks ---
                for i, track_no in enumerate(tracks):
                    if self._cancelled:
                        return _finish_result(
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
                            cache_result=cache_result,
                            test_copy_mismatches=test_copy_mismatches,
                        )

                    idx = unit_index + i
                    album_dir, out_path = build_output_paths(
                        base_dir=base,
                        album_folder_template=job.album_folder_template,
                        filename_template=job.filename_template,
                        album=job.album,
                        track_number=track_no,
                        extension=ext,
                    )
                    album_dir.mkdir(parents=True, exist_ok=True)

                    toc_track = None
                    if job.disc_info is not None:
                        for t in job.disc_info.tracks:
                            if t.number == track_no:
                                toc_track = t
                                break

                    log_entry = None
                    if rip_log is not None:
                        log_entry = rip_log.begin_track(
                            track_no,
                            title=title_for_track(job.album, track_no),
                            length_label=(
                                toc_track.duration_label if toc_track else ''
                            ),
                            start_sector=toc_track.start_sector if toc_track else 0,
                            length_sectors=(
                                toc_track.length_sectors if toc_track else 0
                            ),
                        )

                    expected = expected_sizes.get(track_no)
                    test_wav = tmp_path / f'track{track_no:02d}_test.wav'
                    copy_wav = tmp_path / f'track{track_no:02d}_copy.wav'

                    test_crc = ''
                    copy_crc = ''
                    peak: float | None = None
                    mode = 'secure'
                    elapsed = 0.0
                    matched = not job.test_and_copy
                    last_err: str | None = None
                    track_stats = ParanoiaStats()

                    attempts = 1 + (job.test_copy_max_retries if job.test_and_copy else 0)
                    for attempt in range(attempts):
                        if self._cancelled:
                            break
                        try:
                            attempt_stats = ParanoiaStats()
                            # --- Test pass ---
                            if job.test_and_copy:
                                report(
                                    track_no,
                                    RipState.RIPPING,
                                    index=idx,
                                    sub=0.0,
                                    message=(
                                        f'Testing track {track_no}'
                                        + (
                                            f' (retry {attempt})'
                                            if attempt
                                            else ''
                                        )
                                        + '…'
                                    ),
                                    total_units=total_units,
                                )
                                t0 = time.monotonic()
                                mode, st = self._rip_track(
                                    job.device,
                                    track_no,
                                    test_wav,
                                    expected_bytes=expected,
                                    min_ratio=job.min_wav_size_ratio,
                                    burst_fallback=job.burst_fallback,
                                    sample_offset=job.sample_offset,
                                    on_burst=lambda: report(
                                        track_no,
                                        RipState.RIPPING,
                                        index=idx,
                                        sub=0.08,
                                        message=(
                                            f'Secure test struggled on track '
                                            f'{track_no} — burst mode…'
                                        ),
                                        total_units=total_units,
                                    ),
                                )
                                attempt_stats.merge(st)
                                test_crc, peak, _ = analyze_wav_for_log(
                                    test_wav,
                                    toc_track.length_sectors if toc_track else 0,
                                )
                                if defeat_cache:
                                    report(
                                        track_no,
                                        RipState.RIPPING,
                                        index=idx,
                                        sub=0.22,
                                        message='Defeating drive cache…',
                                        total_units=total_units,
                                    )
                                    flush_drive_cache(job.device, job.disc_info)

                            # --- Copy pass ---
                            report(
                                track_no,
                                RipState.RIPPING,
                                index=idx,
                                sub=0.28 if job.test_and_copy else 0.0,
                                message=(
                                    f'Copying track {track_no}…'
                                    if job.test_and_copy
                                    else f'Extracting track {track_no}…'
                                ),
                                total_units=total_units,
                            )
                            t0 = time.monotonic()
                            mode, st = self._rip_track(
                                job.device,
                                track_no,
                                copy_wav,
                                expected_bytes=expected,
                                min_ratio=job.min_wav_size_ratio,
                                burst_fallback=job.burst_fallback,
                                sample_offset=job.sample_offset,
                                on_burst=lambda: report(
                                    track_no,
                                    RipState.RIPPING,
                                    index=idx,
                                    sub=0.35,
                                    message=(
                                        f'Secure copy struggled on track '
                                        f'{track_no} — burst mode…'
                                    ),
                                    total_units=total_units,
                                ),
                            )
                            attempt_stats.merge(st)
                            elapsed = time.monotonic() - t0
                            copy_crc, peak2, _ = analyze_wav_for_log(
                                copy_wav,
                                toc_track.length_sectors if toc_track else 0,
                            )
                            if peak is None:
                                peak = peak2
                            elif peak2 is not None:
                                peak = max(peak, peak2)

                            if not job.test_and_copy:
                                matched = True
                                test_crc = copy_crc
                                track_stats = attempt_stats
                                break

                            if test_crc and copy_crc and test_crc == copy_crc:
                                matched = True
                                track_stats = attempt_stats
                                break

                            matched = False
                            last_err = (
                                f'Test/Copy CRC mismatch on track {track_no}: '
                                f'test={test_crc} copy={copy_crc}'
                            )
                            log.warning('%s', last_err)
                            notes.append(last_err)
                            track_stats = attempt_stats
                            # Remove partials before retry
                            for p in (test_wav, copy_wav):
                                try:
                                    p.unlink(missing_ok=True)
                                except OSError:
                                    pass
                        except Exception as exc:  # noqa: BLE001
                            last_err = str(exc)
                            log.warning(
                                'Track %s attempt %s failed: %s',
                                track_no,
                                attempt,
                                exc,
                            )
                            if attempt + 1 >= attempts:
                                raise

                    if self._cancelled:
                        return _finish_result(
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
                            cache_result=cache_result,
                            test_copy_mismatches=test_copy_mismatches,
                        )

                    if job.test_and_copy and not matched:
                        test_copy_mismatches.append(track_no)
                        raise RuntimeError(
                            last_err
                            or f'Test & copy failed for track {track_no} '
                            f'after {attempts} attempt(s)'
                        )

                    if mode == 'burst':
                        burst_tracks.append(track_no)
                        notes.append(
                            f'Track {track_no}: ripped in burst mode '
                            f'(secure mode failed; disc may be scratched)'
                        )

                    sectors = toc_track.length_sectors if toc_track else 0
                    if log_entry is not None:
                        log_entry.extract_mode = (
                            f'{mode}+test&copy' if job.test_and_copy else mode
                        )
                        log_entry.extract_seconds = elapsed
                        log_entry.extract_speed_x = extraction_speed_x(
                            sectors, elapsed
                        )
                        log_entry.copy_crc = copy_crc
                        log_entry.test_crc = test_crc
                        log_entry.peak_percent = peak
                        log_entry.quality_percent = track_stats.quality_percent(
                            sectors
                        )
                        log_entry.error_correction_lines = track_stats.summary_lines(
                            length_sectors=sectors
                        )
                        log_entry.had_errors = track_stats.had_errors or (
                            mode == 'burst'
                        )
                        if track_stats.had_errors:
                            log_entry.status = 'finished with errors'
                        try:
                            log_entry.wav_bytes = copy_wav.stat().st_size
                        except OSError:
                            pass
                        if job.test_and_copy and matched:
                            log_entry.notes.append(
                                'Test and copy CRCs match'
                            )
                        if mode == 'burst':
                            log_entry.notes.append(
                                'Secure paranoia failed — burst (-Z) used'
                            )

                    # AccurateRip on the verified copy
                    if ar_verifier is not None:
                        report(
                            track_no,
                            RipState.VERIFYING,
                            index=idx,
                            sub=0.55,
                            message=f'AccurateRip check track {track_no}…',
                            total_units=total_units,
                        )
                        ar = ar_verifier.verify_wav(copy_wav, track_no)
                        ar_results.append(ar)
                        if log_entry is not None:
                            log_entry.accuraterip = ar
                        report(
                            track_no,
                            RipState.VERIFYING,
                            index=idx,
                            sub=0.58,
                            message=ar.message,
                            total_units=total_units,
                        )

                    report(
                        track_no,
                        RipState.ENCODING,
                        index=idx,
                        sub=0.62,
                        message=f'Encoding track {track_no} to {fmt.upper()}…',
                        total_units=total_units,
                    )
                    if fmt == 'wav':
                        shutil.copy2(copy_wav, out_path)
                    else:
                        self._encode(fmt, copy_wav, out_path, job)

                    if self._cancelled:
                        return _finish_result(
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
                            cache_result=cache_result,
                            test_copy_mismatches=test_copy_mismatches,
                        )

                    report(
                        track_no,
                        RipState.TAGGING,
                        index=idx,
                        sub=0.88,
                        message=f'Tagging track {track_no}…',
                        path=out_path,
                        total_units=total_units,
                    )
                    if job.album is not None:
                        total_for_tags = (
                            len(job.album.tracks)
                            if job.album.tracks
                            else disc_track_count
                        )
                        self._tags.write_album_tags(
                            out_path,
                            job.album,
                            track_no,
                            total_tracks=total_for_tags,
                        )
                    if job.embed_artwork and embed_art is not None:
                        self._tags.embed_artwork(
                            out_path,
                            embed_art.data,
                            embed_art.mime,
                        )

                    for p in (test_wav, copy_wav):
                        try:
                            p.unlink(missing_ok=True)
                        except OSError:
                            pass

                    outputs.append(out_path)
                    track_output_pairs.append((track_no, out_path))
                    if log_entry is not None:
                        log_entry.output_path = out_path
                        log_entry.filename = out_path.name
                        log_entry.status = 'OK'
                    report(
                        track_no,
                        RipState.DONE,
                        index=idx,
                        sub=1.0,
                        message=(
                            f'Finished track {track_no}'
                            + (
                                f' (test=copy CRC {copy_crc})'
                                if job.test_and_copy and copy_crc
                                else ''
                            )
                        ),
                        path=out_path,
                        total_units=total_units,
                    )

                # ReplayGain: track + album on complete set
                if job.apply_replaygain and outputs and not self._cancelled:
                    report(
                        tracks[-1] if tracks else 0,
                        RipState.REPLAYGAIN,
                        index=total_units - 1,
                        sub=1.0,
                        message='Analyzing ReplayGain (track + album)…',
                        total_units=total_units,
                    )
                    # Only audio tracks for ReplayGain (skip .cue if already present).
                    audio_for_rg = [
                        p
                        for p in outputs
                        if p.suffix.lower() in {'.flac', '.mp3', '.opus', '.wav'}
                    ]
                    rg_notes = apply_replaygain(audio_for_rg or outputs)
                    notes.extend(rg_notes)
                    if rip_log is not None:
                        rip_log.replaygain_notes.extend(rg_notes)

                # EAC recommendation: multi-file CUE for secure per-track rips.
                if (
                    job.write_cue_file
                    and track_output_pairs
                    and album_dir is not None
                    and not self._cancelled
                ):
                    try:
                        cue_path = self._write_track_cue(
                            job,
                            album_dir=album_dir,
                            track_files=track_output_pairs,
                            htoa_path=htoa_path if htoa_ripped else None,
                            ext=ext,
                        )
                        if cue_path is not None:
                            outputs.append(cue_path)
                            notes.append(f'CUE sheet: {cue_path.name}')
                            if rip_log is not None:
                                rip_log.notes.append(
                                    f'CUE sheet (multi-file, left-out gaps): '
                                    f'{cue_path.name}'
                                )
                    except Exception as cue_exc:  # noqa: BLE001
                        log.warning('Failed to write multi-file CUE: %s', cue_exc)
                        notes.append(f'CUE sheet warning: {cue_exc}')

        except Exception as exc:  # noqa: BLE001
            log.exception('Rip failed')
            return _finish_result(
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
                cache_result=cache_result,
                test_copy_mismatches=test_copy_mismatches,
            )

        if self._cancelled:
            return _finish_result(
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
                cache_result=cache_result,
                test_copy_mismatches=test_copy_mismatches,
            )

        ar_summary = _summarize_ar(ar_results)
        done_msg = f'Done — {len(outputs)} file(s)'
        if htoa_ripped:
            done_msg = f'{done_msg} (incl. HTOA)'
        if ar_summary:
            done_msg = f'{done_msg} · {ar_summary}'
        if burst_tracks:
            done_msg = (
                f'{done_msg} · burst fallback on '
                f'{len(burst_tracks)} track(s)'
            )
        if job.test_and_copy:
            done_msg = f'{done_msg} · test & copy OK'

        result = _finish_result(
            success=True,
            outputs=outputs,
            album_dir=album_dir,
            ar_results=ar_results,
            notes=notes,
            burst_tracks=burst_tracks,
            rip_log=rip_log,
            cover_path=cover_path,
            htoa_ripped=htoa_ripped,
            cache_result=cache_result,
            test_copy_mismatches=test_copy_mismatches,
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
                    current_path=album_dir,
                )
            )
        return result

    def _rip_htoa_unit(
        self,
        *,
        job: RipJob,
        htoa_info,
        tmp_path: Path,
        album_dir: Path,
        fmt: str,
        ext: str,
        embed_art: ArtworkImage | None,
        defeat_cache: bool,
        rip_log: RipLog | None,
        report: Callable,
    ) -> tuple[bool, Path | None, list[str]]:
        """Test & copy HTOA; skip if digitally silent. Returns (ok, path, notes)."""
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

        try:
            report(RipState.RIPPING, 0.05, f'Extracting HTOA ({htoa_info.duration_label})…')
            extract_htoa(
                job.device,
                htoa_info,
                test_wav,
                mode='secure',
                sample_offset=job.sample_offset,
            )
        except Exception as exc:  # noqa: BLE001
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
                except Exception as exc2:  # noqa: BLE001
                    notes.append(f'HTOA extract failed: {exc2}')
                    if log_entry is not None:
                        log_entry.status = 'FAILED'
                        log_entry.notes.append(str(exc2))
                    return False, None, notes
            else:
                notes.append(f'HTOA extract failed: {exc}')
                if log_entry is not None:
                    log_entry.status = 'FAILED'
                    log_entry.notes.append(str(exc))
                return False, None, notes

        if is_digitally_silent(test_wav):
            notes.append(
                f'HTOA pregap is digitally silent '
                f'({htoa_info.length_sectors} sectors) — not saved'
            )
            if log_entry is not None:
                log_entry.status = 'SKIPPED (silence)'
                log_entry.notes.append('Digitally silent pregap')
            try:
                test_wav.unlink(missing_ok=True)
            except OSError:
                pass
            return False, None, notes

        notes.append(
            f'Non-silent HTOA detected ({htoa_info.duration_label}) — ripping'
        )
        htoa_info.is_silent = False

        test_crc = ''
        copy_crc = ''
        peak = None
        mode = 'secure'

        if job.test_and_copy:
            test_crc, peak, _ = analyze_wav_for_log(
                test_wav, htoa_info.length_sectors
            )
            if defeat_cache:
                report(RipState.RIPPING, 0.25, 'Defeating drive cache before HTOA copy…')
                flush_drive_cache(job.device, job.disc_info)
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
            copy_crc, peak2, _ = analyze_wav_for_log(
                copy_wav, htoa_info.length_sectors
            )
            if peak is None:
                peak = peak2
            if test_crc != copy_crc:
                # One retry
                if defeat_cache:
                    flush_drive_cache(job.device, job.disc_info)
                extract_htoa(
                    job.device,
                    htoa_info,
                    copy_wav,
                    mode=mode,
                    sample_offset=job.sample_offset,
                )
                copy_crc, peak2, _ = analyze_wav_for_log(
                    copy_wav, htoa_info.length_sectors
                )
                if test_crc != copy_crc:
                    notes.append(
                        f'HTOA test/copy CRC mismatch '
                        f'(test={test_crc} copy={copy_crc}); keeping copy'
                    )
            wav_for_encode = copy_wav
        else:
            wav_for_encode = test_wav
            copy_crc, peak, _ = analyze_wav_for_log(
                test_wav, htoa_info.length_sectors
            )
            test_crc = copy_crc

        # Output path: track 00
        out_path = album_dir / f'00 - {title}{ext if ext.startswith(".") else f".{ext}"}'
        # Sanitize filename lightly
        safe_name = ''.join(
            c if c not in '<>:"/\\|?*' else '_' for c in out_path.name
        )
        out_path = album_dir / safe_name

        report(RipState.ENCODING, 0.7, f'Encoding HTOA to {fmt.upper()}…')
        if fmt == 'wav':
            shutil.copy2(wav_for_encode, out_path)
        else:
            self._encode(fmt, wav_for_encode, out_path, job)

        report(RipState.TAGGING, 0.9, 'Tagging HTOA…', path=out_path)
        if job.album is not None:
            # Write as track 0 with HTOA title via temporary metadata override
            self._tags.write_album_tags(
                out_path,
                job.album,
                0,
                total_tracks=job.disc_track_count or None,
            )
            # Fix title to HTOA (track 0 often not in MB list)
            _retag_title(out_path, title, track_number=0)
        if job.embed_artwork and embed_art is not None:
            self._tags.embed_artwork(out_path, embed_art.data, embed_art.mime)

        if log_entry is not None:
            log_entry.extract_mode = (
                f'{mode}+test&copy' if job.test_and_copy else mode
            )
            log_entry.copy_crc = copy_crc
            log_entry.test_crc = test_crc
            log_entry.peak_percent = peak
            log_entry.output_path = out_path
            log_entry.filename = out_path.name
            log_entry.status = 'OK'
            log_entry.notes.append('Hidden Track One Audio (non-silent pregap)')

        for p in (test_wav, copy_wav):
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass

        notes.append(f'HTOA saved: {out_path.name}')
        report(RipState.DONE, 1.0, f'Finished HTOA → {out_path.name}', path=out_path)
        return True, out_path, notes

    def _write_track_cue(
        self,
        job: RipJob,
        *,
        album_dir: Path,
        track_files: list[tuple[int, Path]],
        htoa_path: Path | None,
        ext: str,
    ) -> Path | None:
        """Write EAC multi-file CUE for per-track outputs. Returns cue path or None."""
        pairs = [(n, p) for n, p in track_files if n > 0]
        if not pairs:
            return None

        basename = multi_file_cue_basename(job.album, job.disc_info)
        cue_path = album_dir / f'{basename}.cue'
        write_multi_file_cue_sheet(
            cue_path,
            track_files=pairs,
            file_type=cue_file_type_for_extension(ext),
            disc=job.disc_info,
            album=job.album,
            htoa_file=htoa_path if htoa_path is not None else None,
        )
        return cue_path

    def _missing_encoder(self, fmt: str) -> str | None:
        if fmt == 'flac' and not shutil.which('flac'):
            return 'flac encoder not found'
        if fmt == 'mp3' and not shutil.which('lame'):
            return 'lame encoder not found'
        if fmt == 'opus':
            if not shutil.which('opusenc') and not shutil.which('ffmpeg'):
                return 'opusenc or ffmpeg required for Opus'
        return None

    def _rip_track(
        self,
        device: str,
        track_number: int,
        wav_path: Path,
        *,
        expected_bytes: int | None = None,
        min_ratio: float = 0.98,
        burst_fallback: bool = True,
        sample_offset: int = 0,
        on_burst: Callable[[], None] | None = None,
    ) -> tuple[str, ParanoiaStats]:
        """Extract one track. Returns ``(mode, paranoia_stats)``."""
        secure_timeout = _extract_timeout(expected_bytes, mode='secure')
        burst_timeout = _extract_timeout(expected_bytes, mode='burst')
        secure_err: str | None = None
        secure_stats = ParanoiaStats()

        # Prefer a complete secure WAV even when cdparanoia exits non-zero
        # (e.g. -X after exhausted retries — file may still be full length).
        try:
            secure_stats = self._run_cdparanoia(
                device,
                track_number,
                wav_path,
                mode='secure',
                timeout=secure_timeout,
                allow_nonzero=True,
                sample_offset=sample_offset,
            )
            self._assert_wav_ok(
                wav_path,
                track_number,
                expected_bytes=expected_bytes,
                min_ratio=min_ratio,
            )
            return 'secure', secure_stats
        except Exception as exc:  # noqa: BLE001
            if self._cancelled:
                raise
            secure_err = str(exc)
            log.warning(
                'Secure rip failed for track %s: %s',
                track_number,
                secure_err,
            )
            # Keep a full-size secure file if we already have one.
            try:
                self._assert_wav_ok(
                    wav_path,
                    track_number,
                    expected_bytes=expected_bytes,
                    min_ratio=min_ratio,
                )
                log.info(
                    'Track %s: accepting secure WAV despite cdparanoia error',
                    track_number,
                )
                return 'secure', secure_stats
            except Exception:  # noqa: BLE001
                pass

        if not burst_fallback:
            raise RuntimeError(
                f'Secure rip failed for track {track_number}: {secure_err}'
            )

        if on_burst is not None:
            on_burst()

        try:
            if wav_path.exists():
                wav_path.unlink()
        except OSError:
            pass

        try:
            burst_stats = self._run_cdparanoia(
                device,
                track_number,
                wav_path,
                mode='burst',
                timeout=burst_timeout,
                allow_nonzero=True,
                sample_offset=sample_offset,
            )
            self._assert_wav_ok(
                wav_path,
                track_number,
                expected_bytes=expected_bytes,
                min_ratio=max(0.5, min_ratio * 0.85),
                label='burst',
            )
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f'Secure and burst rip both failed for track {track_number}. '
                f'Secure: {secure_err}; burst: {exc}'
            ) from exc

        log.info('Track %s recovered via burst mode', track_number)
        # Prefer burst stats; annotate that secure failed.
        combined = secure_stats.merge(burst_stats) if secure_stats.reads else burst_stats
        return 'burst', combined

    def _run_copy_image(
        self,
        job: RipJob,
        base: Path,
        fmt: str,
        on_progress: ProgressCallback | None,
    ) -> RipResult:
        """EAC-style Copy Image.

        Rips a continuous sector span to one file and encodes (FLAC/WAV).
        When *job.write_cue_file* is set, also writes a matching ``.cue``.
        Track 1 is never extended with a standard 2s pause; extended non-silent
        HTOA may start the image with INDEX 00.
        """
        notes: list[str] = []
        disc = job.disc_info
        if disc is None or not disc.tracks:
            return RipResult(success=False, error='No disc TOC for Copy Image')

        def report(
            state: RipState,
            frac: float,
            message: str,
            path: Path | None = None,
        ) -> None:
            if on_progress is None:
                return
            on_progress(
                RipProgress(
                    track_number=0,
                    state=state,
                    fraction=max(0.0, min(1.0, frac)),
                    message=message,
                    current_path=path,
                )
            )

        # Album folder (reuse track-1 path builder for consistent layout).
        ext = self.EXTENSIONS[fmt]
        album_dir, _ = build_output_paths(
            base_dir=base,
            album_folder_template=job.album_folder_template,
            filename_template=job.filename_template,
            album=job.album,
            track_number=1,
            extension=ext,
        )
        try:
            album_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return RipResult(success=False, error=f'Cannot create album folder: {exc}')

        report(RipState.PREPARING, 0.02, 'Preparing album folder (Copy Image)…', album_dir)
        embed_art, folder_art, cover_path, art_notes = _prepare_artwork(job, album_dir)
        notes.extend(art_notes)

        # Image span: EAC-style — start at track 1 INDEX 01 unless HTOA is
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
        wav_path = album_dir / f'{basename}.wav'
        out_path = album_dir / f'{basename}{ext}'
        cue_path = album_dir / f'{basename}.cue'

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
                'Mode: Copy Image (EAC-style)'
                + (' + CUE sheet' if job.write_cue_file else '')
            )

        try:
            with tempfile.TemporaryDirectory(prefix='ready2rip-img-') as tmp:
                tmp_path = Path(tmp)
                test_wav = tmp_path / 'image_test.wav'
                copy_wav = tmp_path / 'image_copy.wav'

                if self._cancelled:
                    return RipResult(success=False, cancelled=True, notes=notes)

                report(
                    RipState.RIPPING,
                    0.05,
                    f'Extracting disc image ({total_sectors / 75.0 / 60.0:.1f} min)…',
                )
                img_stats = self._run_cdparanoia_span(
                    job.device,
                    file_start,
                    end_sector,
                    test_wav,
                    mode='secure',
                    timeout=max(600, total_sectors // 5),
                    allow_nonzero=True,
                    sample_offset=job.sample_offset,
                )
                self._assert_wav_ok(
                    test_wav,
                    0,
                    expected_bytes=expected_bytes,
                    min_ratio=job.min_wav_size_ratio,
                    label='image secure',
                )

                use_wav = test_wav
                if job.test_and_copy and not self._cancelled:
                    if job.defeat_audio_cache or job.drive_caches_audio:
                        report(RipState.RIPPING, 0.35, 'Defeating drive cache…')
                        flush_drive_cache(job.device, disc)
                    report(RipState.RIPPING, 0.40, 'Copy pass for disc image…')
                    copy_stats = self._run_cdparanoia_span(
                        job.device,
                        file_start,
                        end_sector,
                        copy_wav,
                        mode='secure',
                        timeout=max(600, total_sectors // 5),
                        allow_nonzero=True,
                        sample_offset=job.sample_offset,
                    )
                    img_stats.merge(copy_stats)
                    self._assert_wav_ok(
                        copy_wav,
                        0,
                        expected_bytes=expected_bytes,
                        min_ratio=job.min_wav_size_ratio,
                        label='image copy',
                    )
                    test_crc, _, _ = analyze_wav_for_log(test_wav, total_sectors)
                    copy_crc, _, _ = analyze_wav_for_log(copy_wav, total_sectors)
                    if test_crc and copy_crc and test_crc != copy_crc:
                        notes.append(
                            f'Image test/copy CRC mismatch ({test_crc} ≠ {copy_crc}); '
                            'keeping copy pass'
                        )
                    use_wav = copy_wav

                for line in img_stats.summary_lines(length_sectors=total_sectors):
                    notes.append(line)
                if rip_log is not None:
                    for line in img_stats.summary_lines(length_sectors=total_sectors):
                        rip_log.notes.append(f'Image: {line}')

                if self._cancelled:
                    return RipResult(
                        success=False, cancelled=True, notes=notes, album_dir=album_dir
                    )

                # Move/encode final audio into album folder
                if fmt == 'wav':
                    report(RipState.ENCODING, 0.75, 'Writing image WAV…', out_path)
                    if out_path.exists():
                        out_path.unlink()
                    shutil.copy2(use_wav, out_path)
                else:
                    report(
                        RipState.ENCODING,
                        0.75,
                        f'Encoding disc image to {fmt.upper()}…',
                        out_path,
                    )
                    self._encode(fmt, use_wav, out_path, job)

                if not out_path.is_file() or out_path.stat().st_size < 1000:
                    return RipResult(
                        success=False,
                        error='Image encode produced no output',
                        notes=notes,
                        album_dir=album_dir,
                    )

                # Optional album-level tags on the image (single album file).
                if job.album is not None:
                    report(RipState.TAGGING, 0.88, 'Tagging disc image…', out_path)
                    try:
                        self._tags.write_album_tags(
                            out_path,
                            album=job.album,
                            track_number=1,
                            total_tracks=disc.track_count,
                        )
                        if job.embed_artwork and embed_art is not None:
                            self._tags.embed_artwork(out_path, embed_art)
                    except Exception as exc:  # noqa: BLE001
                        notes.append(f'Image tagging warning: {exc}')

                output_files: list[Path] = [out_path]
                if job.write_cue_file:
                    report(RipState.ENCODING, 0.92, 'Writing CUE sheet…', cue_path)
                    write_cue_sheet(
                        cue_path,
                        image_filename=out_path.name,
                        file_type=cue_file_type_for_extension(ext),
                        disc=disc,
                        album=job.album,
                        file_start_sector=file_start,
                        htoa_index00=htoa_in_image,
                    )
                    notes.append(f'CUE sheet: {cue_path.name}')
                    output_files.append(cue_path)
                    if rip_log is not None:
                        rip_log.notes.append(f'CUE sheet (image): {cue_path.name}')
                else:
                    notes.append('CUE sheet skipped (Write .cue file is off)')

                log_path = None
                if rip_log is not None:
                    try:
                        log_path = rip_log.write(album_dir)
                    except Exception as exc:  # noqa: BLE001
                        notes.append(f'Rip log warning: {exc}')

                done_label = out_path.name
                if job.write_cue_file:
                    done_label = f'{out_path.name} + {cue_path.name}'
                report(
                    RipState.DONE,
                    1.0,
                    f'Copy Image complete → {done_label}',
                    album_dir,
                )
                return RipResult(
                    success=True,
                    output_files=output_files,
                    album_dir=album_dir,
                    notes=notes,
                    log_path=log_path,
                    cover_path=cover_path,
                    htoa_ripped=htoa_in_image,
                )
        except Exception as exc:  # noqa: BLE001
            log.exception('Copy Image failed')
            return RipResult(
                success=False,
                error=str(exc),
                notes=notes,
                album_dir=album_dir,
                cancelled=self._cancelled,
            )

    def _paranoia_cmd_base(
        self,
        binary: str,
        device: str,
        *,
        mode: str,
        sample_offset: int = 0,
    ) -> list[str]:
        """Build shared cdparanoia argv for EAC-like secure or burst extract."""
        cmd = [binary, '-w', '-d', device]
        if sample_offset:
            # Apply drive sample offset at read time (EAC “Read offset correction”).
            cmd.extend(['-O', str(int(sample_offset))])
        if mode == 'burst':
            # Fast path: no paranoia, quiet.
            cmd.extend(['-Z', '-q'])
        else:
            # Full paranoia + EAC-like persistence:
            #  --never-skip=N  re-read imperfect data (do not soft-skip early)
            #  -X              abort if a skip is still forced (no silent holes)
            #  -e              progress/error-correction callbacks on stderr
            # never-skip=N must be one argv token ("-z" "200" is misparsed as track 200).
            cmd.extend(
                [
                    f'--never-skip={SECURE_NEVER_SKIP}',
                    '-X',
                    '-e',
                ]
            )
        return cmd

    def _run_cdparanoia(
        self,
        device: str,
        track_number: int,
        wav_path: Path,
        *,
        mode: str,
        timeout: int,
        allow_nonzero: bool = False,
        sample_offset: int = 0,
    ) -> ParanoiaStats:
        binary = find_cdparanoia()
        if not binary:
            raise RuntimeError('cdparanoia not found')
        cmd = self._paranoia_cmd_base(
            binary, device, mode=mode, sample_offset=sample_offset
        )
        cmd.extend([str(track_number), str(wav_path)])
        return self._run_paranoia(
            cmd,
            what=f'cdparanoia {mode} track {track_number}',
            timeout=timeout,
            allow_nonzero=allow_nonzero,
        )

    def _run_cdparanoia_span(
        self,
        device: str,
        start_sector: int,
        end_sector: int,
        wav_path: Path,
        *,
        mode: str,
        timeout: int,
        allow_nonzero: bool = False,
        sample_offset: int = 0,
    ) -> ParanoiaStats:
        """Extract absolute sector range ``[start, end)`` to *wav_path*.

        libcdio-paranoia parses ``[.A]-[.B]`` as start sector *A* and a
        *relative* end offset *B* (last inclusive sector = A+B) — not absolute
        end sector B. Passing absolute B made Copy Image request past the
        lead-out (“Time/sector offset goes beyond end of disc”).
        """
        binary = find_cdparanoia()
        if not binary:
            raise RuntimeError('cdparanoia not found')
        start = int(start_sector)
        end = int(end_sector)
        if end <= start:
            raise RuntimeError(
                f'Invalid sector span [{start}, {end}) for disc image'
            )
        # N = end - start sectors → last inclusive = end - 1 → relative B.
        rel_end = end - start - 1
        span = f'[.{start}]-[.{rel_end}]'
        cmd = self._paranoia_cmd_base(
            binary, device, mode=mode, sample_offset=sample_offset
        )
        cmd.extend([span, str(wav_path)])
        return self._run_paranoia(
            cmd,
            what=f'cdparanoia {mode} span {span} (sectors [{start}, {end}))',
            timeout=timeout,
            allow_nonzero=allow_nonzero,
        )

    def _assert_wav_ok(
        self,
        wav_path: Path,
        track_number: int,
        *,
        expected_bytes: int | None,
        min_ratio: float,
        label: str = 'secure',
    ) -> None:
        if not wav_path.is_file():
            raise RuntimeError(
                f'{label} extraction produced no file for track {track_number}'
            )
        size = wav_path.stat().st_size
        if size < 1000:
            raise RuntimeError(
                f'{label} extraction produced empty data for track {track_number}'
            )
        if expected_bytes and expected_bytes > 0:
            minimum = int(expected_bytes * min_ratio)
            if size < minimum:
                raise RuntimeError(
                    f'{label} extraction incomplete for track {track_number}: '
                    f'{size} bytes < {minimum} expected '
                    f'({min_ratio:.0%} of ~{expected_bytes} CDDA bytes)'
                )

    def _encode(self, fmt: str, wav_path: Path, out_path: Path, job: RipJob) -> None:
        if out_path.exists():
            out_path.unlink()

        if fmt == 'flac':
            level = max(0, min(8, int(job.flac_compression)))
            cmd = [
                'flac',
                f'-{level}',
                '--silent',
                '-o',
                str(out_path),
                str(wav_path),
            ]
            self._run(cmd, what='flac')
        elif fmt == 'mp3':
            bitrate = int(job.mp3_bitrate)
            cmd = [
                'lame',
                '--silent',
                '-b',
                str(bitrate),
                str(wav_path),
                str(out_path),
            ]
            self._run(cmd, what='lame')
        elif fmt == 'opus':
            if shutil.which('opusenc'):
                cmd = [
                    'opusenc',
                    '--quiet',
                    '--bitrate',
                    str(int(job.opus_bitrate)),
                    str(wav_path),
                    str(out_path),
                ]
                self._run(cmd, what='opusenc')
            else:
                cmd = [
                    'ffmpeg',
                    '-y',
                    '-loglevel',
                    'error',
                    '-i',
                    str(wav_path),
                    '-c:a',
                    'libopus',
                    '-b:a',
                    f'{int(job.opus_bitrate)}k',
                    str(out_path),
                ]
                self._run(cmd, what='ffmpeg opus')
        else:
            raise RuntimeError(f'Unsupported encode format: {fmt}')

        if not out_path.is_file() or out_path.stat().st_size < 100:
            raise RuntimeError(f'Encoder produced no output: {out_path}')

    def _run_paranoia(
        self,
        cmd: list[str],
        *,
        what: str,
        timeout: int | None = None,
        allow_nonzero: bool = False,
    ) -> ParanoiaStats:
        """Run cdparanoia, parse ``-e`` progress into :class:`ParanoiaStats`."""
        code, stderr = self._run_capture(cmd, what=what, timeout=timeout)
        stats = parse_paranoia_stderr(stderr, exit_code=code)
        if self._cancelled:
            return stats
        if code != 0:
            detail = (stderr or '').strip() or f'exit {code}'
            # Prefer a short non-progress diagnostic for the exception text.
            if stats.raw_excerpt:
                detail = stats.raw_excerpt
            elif len(detail) > 400:
                detail = detail[:400] + '…'
            if allow_nonzero:
                log.warning(
                    '%s exited %s (will validate output); paranoia=%s',
                    what,
                    code,
                    stats.short_status(),
                )
                return stats
            raise RuntimeError(f'{what} failed: {detail}')
        return stats

    def _run(
        self,
        cmd: list[str],
        *,
        what: str,
        timeout: int | None = None,
        allow_nonzero: bool = False,
    ) -> None:
        code, stderr = self._run_capture(cmd, what=what, timeout=timeout)
        if self._cancelled:
            return
        if code != 0:
            detail = (stderr or '').strip() or f'exit {code}'
            if len(detail) > 400:
                detail = detail[:400] + '…'
            if allow_nonzero:
                log.warning('%s exited %s (will validate output): %s', what, code, detail)
                return
            raise RuntimeError(f'{what} failed: {detail}')

    def _run_capture(
        self,
        cmd: list[str],
        *,
        what: str,
        timeout: int | None = None,
    ) -> tuple[int, str]:
        log.info('Running %s: %s', what, ' '.join(cmd))
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                _stdout, stderr = self._proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                try:
                    self._proc.communicate(timeout=5)
                except Exception:  # noqa: BLE001
                    pass
                raise RuntimeError(f'{what} timed out after {timeout}s') from None
            code = self._proc.returncode if self._proc is not None else -1
            return code if code is not None else -1, stderr or ''
        except OSError as exc:
            raise RuntimeError(f'Failed to run {what}: {exc}') from exc
        finally:
            self._proc = None


def _prepare_artwork(
    job: RipJob,
    album_dir: Path,
) -> tuple[ArtworkImage | None, ArtworkImage | None, Path | None, list[str]]:
    """Save full-size cover to the album folder; return embed + folder art.

    Called **before** track extraction begins.
    """
    notes: list[str] = []
    folder_art = job.folder_artwork or job.artwork
    embed_art = job.artwork

    # Prefer keeping a larger image for the folder when both are set.
    if (
        job.folder_artwork is not None
        and job.artwork is not None
        and job.folder_artwork.max_edge >= job.artwork.max_edge
    ):
        folder_art = job.folder_artwork
        embed_art = job.artwork
    elif job.folder_artwork is None and job.artwork is not None:
        folder_art = job.artwork
        # Downscale for embed if needed.
        if job.embed_artwork and job.artwork_max_size > 0:
            if job.artwork.max_edge > job.artwork_max_size:
                embed_art = ArtworkFetcher().resize(
                    job.artwork, job.artwork_max_size
                )
            else:
                embed_art = job.artwork
        else:
            embed_art = job.artwork

    cover_path: Path | None = None
    if job.save_cover_file and folder_art is not None and folder_art.data:
        if folder_art.mime == 'image/png':
            cover_path = album_dir / 'cover.png'
        else:
            cover_path = album_dir / 'cover.jpg'
            # Re-encode non-JPEG as JPEG for a consistent cover.jpg when needed.
            if folder_art.mime not in ('image/jpeg', 'image/jpg') and cover_path.suffix == '.jpg':
                try:
                    from PIL import Image
                    import io

                    with Image.open(io.BytesIO(folder_art.data)) as im:
                        if im.mode in ('RGBA', 'LA', 'P'):
                            rgba = im.convert('RGBA')
                            bg = Image.new('RGB', rgba.size, (255, 255, 255))
                            bg.paste(rgba, mask=rgba.split()[-1])
                            im = bg
                        elif im.mode != 'RGB':
                            im = im.convert('RGB')
                        buf = io.BytesIO()
                        im.save(buf, format='JPEG', quality=92, optimize=True)
                        cover_path.write_bytes(buf.getvalue())
                        notes.append(
                            f'Folder cover written ({im.size[0]}×{im.size[1]})'
                        )
                        return embed_art, folder_art, cover_path, notes
                except Exception:  # noqa: BLE001
                    cover_path = album_dir / 'cover.png'
        try:
            cover_path.write_bytes(folder_art.data)
            notes.append(
                f'Folder cover written '
                f'({folder_art.width}×{folder_art.height} {folder_art.source})'
            )
        except OSError as exc:
            log.warning('Could not write cover file: %s', exc)
            notes.append(f'Could not write cover: {exc}')
            cover_path = None

    if job.embed_artwork and embed_art is None and folder_art is not None:
        if job.artwork_max_size > 0 and folder_art.max_edge > job.artwork_max_size:
            embed_art = ArtworkFetcher().resize(folder_art, job.artwork_max_size)
        else:
            embed_art = folder_art

    if job.embed_artwork and embed_art is not None:
        notes.append(
            f'Embed artwork ready ({embed_art.width}×{embed_art.height})'
        )

    return embed_art, folder_art, cover_path, notes


def _retag_title(path: Path, title: str, *, track_number: int = 0) -> None:
    """Overwrite title / track number for HTOA after generic album tags."""
    try:
        suffix = path.suffix.lower()
        if suffix == '.flac':
            from mutagen.flac import FLAC

            audio = FLAC(path)
            audio['TITLE'] = [title]
            audio['TRACKNUMBER'] = [str(track_number)]
            audio.save()
        elif suffix == '.mp3':
            from mutagen.id3 import ID3, TIT2, TRCK
            from mutagen.mp3 import MP3

            audio = MP3(path, ID3=ID3)
            if audio.tags is None:
                audio.add_tags()
            audio.tags.add(TIT2(encoding=3, text=title))
            audio.tags.add(TRCK(encoding=3, text=str(track_number)))
            audio.save()
        elif suffix in {'.opus', '.ogg'}:
            from mutagen.oggopus import OggOpus
            from mutagen.oggvorbis import OggVorbis

            try:
                audio = OggOpus(path)
            except Exception:  # noqa: BLE001
                audio = OggVorbis(path)
            audio['TITLE'] = [title]
            audio['TRACKNUMBER'] = [str(track_number)]
            audio.save()
    except Exception as exc:  # noqa: BLE001
        log.warning('Could not retag HTOA title on %s: %s', path, exc)


def _expected_wav_sizes(info: DiscInfo | None) -> dict[int, int]:
    """Map track number → approximate WAV byte size (44-byte header + PCM)."""
    if info is None:
        return {}
    sizes: dict[int, int] = {}
    for track in info.tracks:
        pcm = track.length_sectors * 2352
        sizes[track.number] = 44 + pcm
    return sizes


def _extract_timeout(expected_bytes: int | None, *, mode: str) -> int:
    """Wall-clock budget for one track extract based on audio length."""
    # CDDA stereo 16-bit 44.1 kHz = 176400 bytes/sec of PCM (+44 WAV header).
    if expected_bytes and expected_bytes > 1000:
        audio_sec = max(1.0, (expected_bytes - 44) / 176400.0)
    else:
        audio_sec = 300.0
    if mode == 'secure':
        # Paranoia retries; allow up to ~10× realtime, min 10 min, max 90 min.
        return int(min(5400, max(600, audio_sec * 10 + 120)))
    # Burst is faster; still bound by slow drives.
    return int(min(2400, max(300, audio_sec * 4 + 60)))


def _finish_result(
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
    cache_result: DriveCacheResult | None = None,
    test_copy_mismatches: list[int] | None = None,
) -> RipResult:
    log_path: Path | None = None
    if rip_log is not None:
        rip_log.notes.extend(n for n in notes if n not in rip_log.notes)
        rip_log.finish(error=error, cancelled=cancelled)
        dest = album_dir
        if dest is None and outputs:
            dest = outputs[0].parent
        if dest is not None:
            try:
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
        notes=notes,
        burst_tracks=burst_tracks,
        log_path=log_path,
        cover_path=cover_path,
        htoa_ripped=htoa_ripped,
        cache_result=cache_result,
        test_copy_mismatches=test_copy_mismatches or [],
    )


def _summarize_ar(results: list[AccurateRipResult]) -> str:
    if not results:
        return ''
    matches = sum(1 for r in results if r.confidence == AccurateRipConfidence.MATCH)
    mismatches = sum(
        1 for r in results if r.confidence == AccurateRipConfidence.MISMATCH
    )
    missing = sum(
        1 for r in results if r.confidence == AccurateRipConfidence.NOT_IN_DB
    )
    parts = [f'AR {matches}/{len(results)} match']
    if mismatches:
        parts.append(f'{mismatches} mismatch')
    if missing:
        parts.append(f'{missing} not in DB')
    return ', '.join(parts)
