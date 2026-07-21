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
from ready2rip.rip.riplog import (
    RipLog,
    analyze_wav_for_log,
    extraction_speed_x,
    title_for_track,
)
from ready2rip.tags.writer import TagWriter
from ready2rip.util import find_cdparanoia, validate_device_path

log = logging.getLogger(__name__)


class RipState(Enum):
    PENDING = auto()
    PREPARING = auto()
    RIPPING = auto()
    VERIFYING = auto()
    ENCODING = auto()
    TAGGING = auto()
    REPLAYGAIN = auto()
    DONE = auto()
    FAILED = auto()
    CANCELLED = auto()


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
    # Expected minimum WAV size as a fraction of ideal CDDA size (scratched reads).
    min_wav_size_ratio: float = 0.90
    # Write an EAC-style detailed status log next to the ripped files.
    write_rip_log: bool = True
    # Double-rip each track and require matching CRCs (test & copy).
    test_and_copy: bool = True
    # Max extra full test+copy cycles after a CRC mismatch.
    test_copy_max_retries: int = 2
    # Persisted from Drive setup (None = unknown / not measured).
    drive_caches_audio: bool | None = None
    drive_cache_message: str = ''
    drive_accurate_stream: bool | None = None
    drive_accurate_stream_message: str = ''
    drive_c2_pointers: bool | None = None
    drive_c2_message: str = ''
    # Seek/read elsewhere between test and copy when cache is present (or always).
    defeat_audio_cache: bool = True
    # Detect pregap before track 1 and rip non-silent HTOA as track 00.
    rip_htoa: bool = True
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
        if not tracks and not job.rip_htoa:
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

        missing = self._missing_encoder(fmt)
        if missing:
            return RipResult(success=False, error=missing)

        base = job.output_directory.expanduser().resolve()
        try:
            base.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return RipResult(success=False, error=f'Cannot create output folder: {exc}')

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

        # AccurateRip setup
        ar_verifier: AccurateRipVerifier | None = None
        if job.verify_accuraterip and job.disc_info is not None:
            ar_verifier = AccurateRipVerifier(sample_offset=job.sample_offset)
            status = ar_verifier.prepare(job.disc_info)
            notes.append(status)
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

                    attempts = 1 + (job.test_copy_max_retries if job.test_and_copy else 0)
                    for attempt in range(attempts):
                        if self._cancelled:
                            break
                        try:
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
                                mode = self._rip_track(
                                    job.device,
                                    track_no,
                                    test_wav,
                                    expected_bytes=expected,
                                    min_ratio=job.min_wav_size_ratio,
                                    burst_fallback=job.burst_fallback,
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
                                        message=f'Defeating drive cache…',
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
                            mode = self._rip_track(
                                job.device,
                                track_no,
                                copy_wav,
                                expected_bytes=expected,
                                min_ratio=job.min_wav_size_ratio,
                                burst_fallback=job.burst_fallback,
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
                                break

                            if test_crc and copy_crc and test_crc == copy_crc:
                                matched = True
                                break

                            matched = False
                            last_err = (
                                f'Test/Copy CRC mismatch on track {track_no}: '
                                f'test={test_crc} copy={copy_crc}'
                            )
                            log.warning('%s', last_err)
                            notes.append(last_err)
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

                    if log_entry is not None:
                        log_entry.extract_mode = (
                            f'{mode}+test&copy' if job.test_and_copy else mode
                        )
                        log_entry.extract_seconds = elapsed
                        sectors = toc_track.length_sectors if toc_track else 0
                        log_entry.extract_speed_x = extraction_speed_x(
                            sectors, elapsed
                        )
                        log_entry.copy_crc = copy_crc
                        log_entry.test_crc = test_crc
                        log_entry.peak_percent = peak
                        try:
                            log_entry.wav_bytes = copy_wav.stat().st_size
                        except OSError:
                            pass
                        if job.test_and_copy and matched:
                            log_entry.notes.append(
                                'Test and copy CRCs match'
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
                    rg_notes = apply_replaygain(outputs)
                    notes.extend(rg_notes)
                    if rip_log is not None:
                        rip_log.replaygain_notes.extend(rg_notes)

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
            extract_htoa(job.device, htoa_info, test_wav, mode='secure')
        except Exception as exc:  # noqa: BLE001
            if job.burst_fallback:
                try:
                    extract_htoa(job.device, htoa_info, test_wav, mode='burst')
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
                extract_htoa(job.device, htoa_info, copy_wav, mode='secure')
            except Exception:
                if job.burst_fallback:
                    extract_htoa(job.device, htoa_info, copy_wav, mode='burst')
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
                extract_htoa(job.device, htoa_info, copy_wav, mode=mode)
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
        min_ratio: float = 0.90,
        burst_fallback: bool = True,
        on_burst: Callable[[], None] | None = None,
    ) -> str:
        """Extract one track. Returns ``'secure'`` or ``'burst'``."""
        secure_timeout = _extract_timeout(expected_bytes, mode='secure')
        burst_timeout = _extract_timeout(expected_bytes, mode='burst')
        secure_err: str | None = None

        # Prefer a complete secure WAV even when cdparanoia exits non-zero
        # (common with -z after sector skips — file is often still full length).
        try:
            self._run_cdparanoia(
                device,
                track_number,
                wav_path,
                mode='secure',
                timeout=secure_timeout,
                allow_nonzero=True,
            )
            self._assert_wav_ok(
                wav_path,
                track_number,
                expected_bytes=expected_bytes,
                min_ratio=min_ratio,
            )
            return 'secure'
        except Exception as exc:  # noqa: BLE001
            if self._cancelled:
                raise
            secure_err = str(exc)
            log.warning(
                'Secure rip failed for track %s: %s',
                track_number,
                secure_err,
            )
            # Keep a full-size secure file if we already have one (non-zero exit
            # with complete data) — do not delete and re-burst needlessly.
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
                return 'secure'
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
            self._run_cdparanoia(
                device,
                track_number,
                wav_path,
                mode='burst',
                timeout=burst_timeout,
                allow_nonzero=True,
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
        return 'burst'

    def _run_cdparanoia(
        self,
        device: str,
        track_number: int,
        wav_path: Path,
        *,
        mode: str,
        timeout: int,
        allow_nonzero: bool = False,
    ) -> None:
        binary = find_cdparanoia()
        if not binary:
            raise RuntimeError('cdparanoia not found')
        cmd = [
            binary,
            '-w',
            '-d',
            device,
        ]
        if mode == 'burst':
            cmd.append('-Z')
            cmd.append('-q')
        else:
            # never-skip=N must be one argv token. Separate "-z" "75" is parsed
            # as track number 75 (cdparanoia track-does-not-exist failure).
            cmd.extend(['--never-skip=75', '-q'])

        cmd.extend([str(track_number), str(wav_path)])
        self._run(
            cmd,
            what=f'cdparanoia {mode} track {track_number}',
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

    def _run(
        self,
        cmd: list[str],
        *,
        what: str,
        timeout: int | None = None,
        allow_nonzero: bool = False,
    ) -> None:
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
            code = self._proc.returncode
        except OSError as exc:
            raise RuntimeError(f'Failed to run {what}: {exc}') from exc
        finally:
            self._proc = None

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
