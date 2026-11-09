from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
import threading
import time
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path

from ready2rip.accuraterip import AccurateRipConfidence, AccurateRipResult, AccurateRipVerifier
from ready2rip.artwork.fetch import ArtworkFetcher, ArtworkImage
from ready2rip.ctdb import (
    CtdbConfidence,
    CtdbResult,
    CtdbVerifier,
    load_cdda_pcm,
    total_audio_samples,
)
from ready2rip.disc.cache import flush_drive_cache
from ready2rip.disc.data import extract_data_tracks
from ready2rip.disc.gaps import enrich_pregaps
from ready2rip.disc.htoa import (
    detect_htoa,
    extract_htoa,
    htoa_display_title,
    is_digitally_silent,
)
from ready2rip.disc.overread import (
    OverreadCapability,
    overread_span,
    per_track_overread_read_span,
    probe_overread,
)
from ready2rip.disc.probe import DiscInfo, attach_mmc_toc
from ready2rip.metadata.lyrics import write_lyrics_sidecar
from ready2rip.metadata.providers import AlbumMetadata
from ready2rip.paths import (
    DEFAULT_ALBUM_FOLDER_TEMPLATE,
    DEFAULT_FILENAME_TEMPLATE,
    build_output_paths,
    track_meta_for,
)
from ready2rip.replaygain import apply_replaygain
from ready2rip.rip.ctdb_quality import ctdb_submit_quality
from ready2rip.rip.cue import (
    cue_file_type_for_extension,
    image_basename,
    multi_file_cue_basename,
    write_cue_sheet,
    write_multi_file_cue_sheet,
)
from ready2rip.rip.encode import (
    aac_encode_argv,
    aiff_encode_argv,
    encoder_tag_for_format,
    flac_encode_argv,
    mp3_encode_argv,
    opus_encode_argv,
    opus_ffmpeg_argv,
    qaac_available,
    qaac_encode_argv,
)
from ready2rip.rip.encode_dest import (
    audio_dir_for_format,
    encode_format_list,
    is_lossless_extension,
    is_lossless_format,
    is_lossy_format,
    should_embed_artwork_for_format,
)
from ready2rip.rip.paranoia_run import run_paranoia_streaming
from ready2rip.rip.paranoia_stats import FRAMES_PER_SECTOR, ParanoiaStats
from ready2rip.rip.progress_map import (
    normalize_unit_weights,
    overall_fraction,
    unit_end_fraction,
)
from ready2rip.rip.progress_pulse import ProgressPulse
from ready2rip.rip.riplog import (
    RipLog,
    TrackLogEntry,
    analyze_pcm_for_log,
    analyze_wav_for_log,
    extraction_speed_x,
    title_for_track,
    wav_pcm_crc,
)
from ready2rip.rip.secure import (
    SECURE_MIN_WAV_RATIO,
    SECURE_TEST_COPY_RETRIES,
    burst_paranoia_flags,
    secure_paranoia_flags,
    wav_size_minimum,
)
from ready2rip.rip.wav_trim import trim_wav_to_expected
from ready2rip.settings import (
    lyrics_mode_writes_sidecar,
    lyrics_mode_writes_tags,
)
from ready2rip.tags.artists import DEFAULT_ARTIST_STYLE
from ready2rip.tags.writer import TagWriter, ripper_tag_value
from ready2rip.util import find_cdparanoia, validate_device_path

log = logging.getLogger(__name__)


class RipState(Enum):
    PREPARING = auto()
    RIPPING = auto()
    VERIFYING = auto()
    ENCODING = auto()
    REPLAYGAIN = auto()
    DONE = auto()


@dataclass
class RipProgress:
    track_number: int
    state: RipState
    fraction: float = 0.0  # overall 0..1
    message: str = ''


@dataclass
class RipResult:
    success: bool
    output_files: list[Path] = field(default_factory=list)
    album_dir: Path | None = None
    error: str | None = None
    cancelled: bool = False
    accuraterip: list[AccurateRipResult] = field(default_factory=list)
    ctdb: list[CtdbResult] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    burst_tracks: list[int] = field(default_factory=list)
    log_path: Path | None = None
    cover_path: Path | None = None
    htoa_ripped: bool = False
    test_copy_mismatches: list[int] = field(default_factory=list)


@dataclass
class RipJob:
    """Description of a full-disc or multi-track rip."""

    device: str
    track_numbers: list[int]
    output_directory: Path
    encode_format: str = 'flac'  # flac | wav | aiff (+ lossy via also-encode)
    flac_compression: int = 5
    mp3_quality: str = 'v0'  # LAME v0–v5 or cbr128/192/256/320
    aac_quality: str = 'cbr256'  # FDK VBR 1–5 or cbr128/192/256/320
    aac_bitrate: int = 256  # legacy; prefer aac_quality
    qaac_quality: str = 'cvbr256'  # popular TVBR / CVBR / CBR
    opus_bitrate: int = 160
    # Optional second (lossy) encode from the same verified WAV.
    also_encode: bool = False
    also_encode_format: str = 'mp3'
    also_encode_folder: str = 'format_subdir'
    # Used when also_encode_folder == 'custom_folder' (second output root).
    also_encode_directory: str = ''
    apply_replaygain: bool = True
    # iTunNORM on MP3 / M4A / AIFF / WAV (skipped for FLAC / Opus).
    write_sound_check: bool = True
    # Embed cover into MP3 / M4A / Opus.
    embed_artwork: bool = True
    # Embed cover into FLAC / WAV / AIFF.
    embed_artwork_lossless: bool = False
    # Lossy embed-sized image (MP3 / M4A / Opus).
    artwork: ArtworkImage | None = None
    # Lossless embed-sized image (FLAC / WAV / AIFF).
    artwork_lossless: ArtworkImage | None = None
    # Full-resolution image for folder cover.jpg (lossless dirs only).
    folder_artwork: ArtworkImage | None = None
    album: AlbumMetadata | None = None
    filename_template: str = DEFAULT_FILENAME_TEMPLATE
    album_folder_template: str = DEFAULT_ALBUM_FOLDER_TEMPLATE
    artist_style: str = DEFAULT_ARTIST_STYLE
    save_cover_file: bool = True
    verify_accuraterip: bool = True
    verify_ctdb: bool = True
    submit_ctdb: bool = False
    sample_offset: int = 0
    # True after Detect or manual save in drive setup (offset may still be 0).
    offset_configured: bool = False
    # Drive can READ CD into lead-in / lead-out (for offset edge samples).
    overread: OverreadCapability = field(default_factory=OverreadCapability)
    disc_info: DiscInfo | None = None
    # Total tracks on the disc (for AR first/last sample skip), not just selected.
    disc_track_count: int = 0
    # Optional insecure -Z recovery after secure failure (off by default).
    burst_fallback: bool = False
    # Expected minimum WAV size as a fraction of ideal CDDA size (near complete).
    min_wav_size_ratio: float = SECURE_MIN_WAV_RATIO
    # Write a detailed status log next to the ripped files.
    write_rip_log: bool = True
    # Write multi-file (or image) CUE sheet after a secure rip (lossless only).
    write_cue_file: bool = True
    # Write ready2rip + version into the comment field (Ripper tag preference).
    # Encoded by / ENCODER / ©too always records the codec tool + version.
    add_ripper_tag: bool = True
    # Save lyrics with lossless encodes (master); mode chooses tags / sidecar / both.
    embed_lyrics: bool = True
    lyrics_save_mode: str = 'both'
    # Double-rip each track and require matching CRCs (test & copy).
    test_and_copy: bool = True
    # Extra secure test+copy cycles after a CRC mismatch.
    # After retries still fail, that track is recovered once in burst mode
    # only when burst_fallback is enabled — otherwise the rip fails.
    test_copy_max_retries: int = SECURE_TEST_COPY_RETRIES
    # Always seek/read elsewhere between test and copy to defeat drive audio cache.
    defeat_audio_cache: bool = True
    # Handle extended track-1 pregap as HTOA (track 00); never
    # prepend that pregap onto track 1. Standard 2s pause is ignored.
    rip_htoa: bool = True
    # Raw MODE1/2352 or MODE2/2352 .bin for mixed-mode / CD-Extra data.
    rip_data_tracks: bool = True
    # Copy Image: one continuous image (instead of per-track files).
    # Pair with write_cue_file for a matching image CUE sheet.
    copy_image: bool = False
    # Longest edge for embeds when preparing from folder art.
    artwork_max_size: int = 600  # lossy embed edge; match settings default
    artwork_max_size_lossless: int = 600

    def wants_embed_artwork(self) -> bool:
        return bool(self.embed_artwork or self.embed_artwork_lossless)

    def should_embed_format(self, fmt: str) -> bool:
        """Whether *fmt* should receive an embedded cover for this job."""
        return should_embed_artwork_for_format(
            fmt,
            embed_lossy=bool(self.embed_artwork),
            embed_lossless=bool(self.embed_artwork_lossless),
        )


@dataclass(frozen=True)
class EmbedArtworkSet:
    """Per-format-family embed images (lossy vs lossless sizes)."""

    lossy: ArtworkImage | None = None
    lossless: ArtworkImage | None = None

    def for_format(self, fmt: str, job: RipJob) -> ArtworkImage | None:
        if is_lossy_format(fmt):
            return self.lossy if job.embed_artwork else None
        if is_lossless_format(fmt):
            return self.lossless if job.embed_artwork_lossless else None
        return None


ProgressCallback = Callable[[RipProgress], None]

# Nested report used during a multi-track job (index/sub → overall fraction).
TrackReportFn = Callable[..., None]


@dataclass
class _TrackExtract:
    """Result of extracting one track to WAV (secure test&copy or burst)."""

    mode: str  # final encode path: secure | burst
    test_crc: str
    copy_crc: str
    peak: float | None
    elapsed: float  # total wall time (all passes / retries)
    stats: ParanoiaStats
    matched: bool  # dual-pass CRCs matched when dual_pass
    copy_wav: Path
    test_wav: Path
    error: str | None = None
    dual_pass: bool = False  # True only when real test&copy was done
    test_mode: str = ''  # mode of the successful test pass
    copy_mode: str = ''  # mode of the successful copy pass
    attempt_log: list[str] = field(default_factory=list)
    expected_wav_bytes: int = 0


@dataclass
class _PendingEncode:
    """Background encode overlapping the next track's extraction."""

    future: Future[list[Path]]
    track_no: int
    idx: float
    dests: list[_EncodeDest]
    extracted: _TrackExtract
    test_wav: Path
    copy_wav: Path
    log_entry: TrackLogEntry | None


@dataclass(frozen=True)
class _EncodeDest:
    """One encode destination resolved to album folder + output file."""

    format: str
    base_dir: Path
    # Directory that holds this format's audio files.
    album_dir: Path
    out_path: Path
    # Album root for cover / log / CUE (may be the parent of album_dir).
    meta_dir: Path
    primary: bool = False

    @property
    def extension(self) -> str:
        return RipEngine.EXTENSIONS[self.format]


class _CrcMismatch(Exception):
    """Secure test&copy failed after retries with no recovered copy WAV."""

    def __init__(
        self,
        track_number: int,
        message: str,
        *,
        test_crc: str = '',
        copy_crc: str = '',
        attempt_log: list[str] | None = None,
    ) -> None:
        super().__init__(message)
        self.track_number = track_number
        self.message = message
        self.test_crc = test_crc
        self.copy_crc = copy_crc
        self.attempt_log = list(attempt_log or [])


class RipEngine:
    """Coordinates test&copy, HTOA, AccurateRip, encode, tag, ReplayGain, art."""

    EXTENSIONS = {
        'flac': '.flac',
        'mp3': '.mp3',
        'aac': '.m4a',
        'qaac': '.m4a',
        'opus': '.opus',
        'wav': '.wav',
        'aiff': '.aiff',
    }

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
        # Drop incomplete encode outputs; the encode worker exits via terminate.
        pending = self._encode_pending
        if pending is not None:
            for dest in pending.dests:
                try:
                    if dest.out_path.is_file() and dest.out_path.stat().st_size < 1024:
                        dest.out_path.unlink(missing_ok=True)
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

        fmt = (job.encode_format or 'flac').strip().lower()
        if fmt not in self.EXTENSIONS:
            fmt = 'flac'
        base = Path(job.output_directory).expanduser().resolve()

        # CD images + CUE need a lossless/container format (WAV/FLAC).
        if job.copy_image and fmt not in ('flac', 'wav', 'aiff'):
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
        ctdb_results: list[CtdbResult] = []
        notes: list[str] = list(layout_notes)
        burst_tracks: list[int] = []
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

        # AccurateRip / CTDB setup. Sample offset is applied at extract time via
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

        ctdb_verifier: CtdbVerifier | None = None
        if (job.verify_ctdb or job.submit_ctdb) and job.disc_info is not None:
            ctdb_verifier = CtdbVerifier()
            ctdb_status = ctdb_verifier.prepare(job.disc_info)
            notes.append(ctdb_status)
            if rip_log is not None:
                rip_log.ctdb_status = ctdb_status
            report(
                first_for_paths,
                RipState.VERIFYING,
                index=0.0,
                sub=0.03,
                message=ctdb_status,
            )

        disc_track_count = job.disc_track_count or (
            job.disc_info.track_count if job.disc_info else (max(tracks) if tracks else 0)
        )
        if ar_verifier is not None:
            ar_verifier.set_total_tracks(disc_track_count)
        if ctdb_verifier is not None and job.disc_info is not None:
            ctdb_verifier.set_totals(
                total_tracks=disc_track_count,
                total_samples=total_audio_samples(job.disc_info),
            )

        # Always defeat drive audio cache between test and copy.
        defeat_cache = bool(job.defeat_audio_cache)
        if defeat_cache:
            notes.append('Cache defeat enabled between test and copy')

        unit_index = 0.0
        htoa_path: Path | None = None
        htoa_paths: list[Path] = []
        # (track_number, path) for multi-file CUE generation after encode.
        track_output_pairs: list[tuple[int, Path]] = []
        data_output_pairs: list[tuple[int, Path]] = []

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
                        ctdb_verifier=ctdb_verifier,
                        disc_track_count=disc_track_count,
                        unit_index=unit_index,
                        total_units=total_units,
                        report=report,
                        outputs=outputs,
                        track_output_pairs=track_output_pairs,
                        ar_results=ar_results,
                        ctdb_results=ctdb_results,
                        notes=notes,
                        burst_tracks=burst_tracks,
                        test_copy_mismatches=test_copy_mismatches,
                        rip_log=rip_log,
                    )
                except _CrcMismatch as crc_exc:
                    if crc_exc.track_number not in test_copy_mismatches:
                        test_copy_mismatches.append(crc_exc.track_number)
                    raise RuntimeError(crc_exc.message) from crc_exc
                if self._cancelled:
                    return _finish_result(
                        success=False,
                        cancelled=True,
                        error='Cancelled',
                        outputs=outputs,
                        album_dir=album_dir,
                        ar_results=ar_results,
                        ctdb_results=ctdb_results,
                        notes=notes,
                        burst_tracks=burst_tracks,
                        rip_log=rip_log,
                        cover_path=cover_path,
                        htoa_ripped=htoa_ripped,
                        test_copy_mismatches=test_copy_mismatches,
                        log_dir=primary_audio_dir,
                    )

                data_index = float(len(tracks) + (1 if htoa_info else 0))
                album_dirs = _unique_album_dirs(outputs, album_dir)
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
                        if not is_lossless_extension(cue_ext):
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
            return _finish_result(
                success=False,
                error=str(exc),
                outputs=outputs,
                album_dir=album_dir,
                ar_results=ar_results,
                ctdb_results=ctdb_results,
                notes=notes,
                burst_tracks=burst_tracks,
                rip_log=rip_log,
                cover_path=cover_path,
                htoa_ripped=htoa_ripped,
                test_copy_mismatches=test_copy_mismatches,
                log_dir=primary_audio_dir,
            )

        if self._cancelled:
            return _finish_result(
                success=False,
                cancelled=True,
                error='Cancelled',
                outputs=outputs,
                album_dir=album_dir,
                ar_results=ar_results,
                ctdb_results=ctdb_results,
                notes=notes,
                burst_tracks=burst_tracks,
                rip_log=rip_log,
                cover_path=cover_path,
                htoa_ripped=htoa_ripped,
                test_copy_mismatches=test_copy_mismatches,
                log_dir=primary_audio_dir,
            )

        if job.submit_ctdb and ctdb_verifier is not None and ctdb_results:
            if on_progress is not None:
                on_progress(
                    RipProgress(
                        track_number=tracks[-1] if tracks else 0,
                        state=RipState.VERIFYING,
                        fraction=0.97,
                        message='Submitting to CTDB…',
                    )
                )
            drivename = ''
            try:
                from ready2rip.disc.drive_info import probe_drive

                drivename = probe_drive(job.device, allow_optical=False).ctdb_drivename
            except Exception:
                drivename = ''
            album = job.album
            ctdb_quality = ctdb_submit_quality(
                test_and_copy=job.test_and_copy,
                burst_tracks=burst_tracks,
                test_copy_mismatches=test_copy_mismatches,
                track_entries=rip_log.tracks if rip_log is not None else None,
            )
            if ctdb_quality < 100:
                notes.append(
                    'CTDB submit quality=0 '
                    '(requires secure test&copy, no burst, '
                    'paranoia quality 100% on all tracks)'
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
            if not submit.ok and any(r.confidence == CtdbConfidence.MISMATCH for r in ctdb_results):
                try:
                    probe = ctdb_verifier.attempt_repair_for_mismatch(ctdb_results)
                    notes.append(probe.message)
                    if rip_log is not None:
                        rip_log.notes.append(probe.message)
                except Exception as exc:
                    log.warning('CTDB repair probe failed: %s', exc)

        ar_summary = _summarize_ar(ar_results)
        ctdb_summary = _summarize_ctdb(ctdb_results)
        done_msg = f'Done — {len(outputs)} file(s)'
        if htoa_ripped:
            done_msg = f'{done_msg} (incl. HTOA)'
        if data_output_pairs:
            done_msg = f'{done_msg} · {len(data_output_pairs)} data'
        if ar_summary:
            done_msg = f'{done_msg} · {ar_summary}'
        if ctdb_summary:
            done_msg = f'{done_msg} · {ctdb_summary}'
        if burst_tracks:
            tracks_txt = ', '.join(str(n) for n in burst_tracks)
            done_msg = f'{done_msg} · WARNING: burst (-Z) on track(s) {tracks_txt}'
            notes.append(
                f'WARNING: Insecure burst extraction on track(s) {tracks_txt} '
                '(not archival-grade for those tracks)'
            )
        if job.test_and_copy:
            if test_copy_mismatches:
                tracks_txt = ', '.join(str(n) for n in test_copy_mismatches)
                done_msg = (
                    f'{done_msg} · test & copy mismatch on track(s) {tracks_txt} (burst recovery)'
                )
            else:
                done_msg = f'{done_msg} · test & copy OK'

        result = _finish_result(
            success=True,
            outputs=outputs,
            album_dir=album_dir,
            ar_results=ar_results,
            ctdb_results=ctdb_results,
            notes=notes,
            burst_tracks=burst_tracks,
            rip_log=rip_log,
            cover_path=cover_path,
            htoa_ripped=htoa_ripped,
            test_copy_mismatches=test_copy_mismatches,
            log_dir=primary_audio_dir,
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

        try:
            report(RipState.RIPPING, 0.05, f'Extracting HTOA ({htoa_info.duration_label})…')
            extract_htoa(
                job.device,
                htoa_info,
                test_wav,
                mode='secure',
                sample_offset=job.sample_offset,
            )
        except Exception as exc:
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
        mode = 'secure'

        if job.test_and_copy:
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
        """Extract mixed-mode / CD-Extra data into each album folder.

        Returns ``(primary_pairs, all_written_paths)``. Raises if ripping
        data is enabled and extract fails.
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

    def _missing_encoder(self, fmt: str) -> str | None:
        if fmt == 'flac' and not shutil.which('flac'):
            return 'flac encoder not found'
        if fmt == 'mp3' and not shutil.which('lame'):
            return 'lame encoder not found'
        if fmt == 'aac' and not shutil.which('ffmpeg'):
            return 'ffmpeg required for AAC'
        if fmt == 'qaac' and not qaac_available():
            return (
                'qaac not found — install Wine + qaac.exe + Apple AAC codecs '
                '(or set READY2RIP_QAAC)'
            )
        if fmt == 'aiff' and not shutil.which('ffmpeg'):
            return 'ffmpeg required for AIFF'
        if fmt == 'opus' and not shutil.which('opusenc') and not shutil.which('ffmpeg'):
            return 'opusenc or ffmpeg required for Opus'
        return None

    def _destinations_for_track(
        self,
        job: RipJob,
        track_number: int,
        *,
        filename: str | None = None,
    ) -> list[_EncodeDest]:
        """Resolve album folder + output path(s) for primary (+ optional also)."""
        formats = encode_format_list(
            job.encode_format,
            also_encode=job.also_encode,
            also_format=job.also_encode_format,
            # Copy Image stays a single lossless/container encode.
            allow_also=not job.copy_image,
        )
        layout = (job.also_encode_folder or 'format_subdir').strip().lower()
        also_root = (job.also_encode_directory or '').strip()
        use_custom_also = len(formats) > 1 and layout == 'custom_folder' and also_root
        # Format subfolders only when both encodes share the same album root.
        multi = len(formats) > 1 and layout == 'format_subdir' and not use_custom_also
        primary_base = Path(job.output_directory).expanduser().resolve()
        dests: list[_EncodeDest] = []
        for i, enc_fmt in enumerate(formats):
            if enc_fmt not in self.EXTENSIONS:
                enc_fmt = 'flac'
            enc_ext = self.EXTENSIONS[enc_fmt]
            if i > 0 and use_custom_also:
                enc_base = Path(also_root).expanduser().resolve()
                folder_layout = 'same_folder'
            else:
                enc_base = primary_base
                if multi:
                    folder_layout = 'format_subdir'
                elif layout == 'same_folder' or use_custom_also:
                    folder_layout = 'same_folder'
                else:
                    # Unknown / empty custom path → keep both in the album folder.
                    folder_layout = 'same_folder'
            meta_dir, templated = build_output_paths(
                base_dir=enc_base,
                album_folder_template=job.album_folder_template,
                filename_template=job.filename_template,
                album=job.album,
                track_number=max(1, track_number),
                extension=enc_ext,
                artist_style=job.artist_style,
            )
            audio_dir = audio_dir_for_format(
                meta_dir,
                enc_fmt,
                folder_layout=folder_layout,
                multi_format=multi,
            )
            if filename:
                stem = Path(filename).name
                if stem.lower().endswith(enc_ext.lower()):
                    out_name = stem
                else:
                    # Strip a known audio suffix then apply this format's ext.
                    stem_path = Path(stem)
                    if stem_path.suffix.lower() in {
                        '.flac',
                        '.wav',
                        '.aiff',
                        '.aif',
                        '.mp3',
                        '.m4a',
                        '.opus',
                    }:
                        out_name = f'{stem_path.stem}{enc_ext}'
                    else:
                        out_name = f'{stem}{enc_ext}'
                out_path = audio_dir / out_name
            else:
                out_path = audio_dir / templated.name
            dests.append(
                _EncodeDest(
                    format=enc_fmt,
                    base_dir=enc_base,
                    album_dir=audio_dir,
                    out_path=out_path,
                    meta_dir=meta_dir,
                    primary=i == 0,
                )
            )
        return dests

    def _ensure_destination_dirs(self, dests: list[_EncodeDest]) -> None:
        """Create every destination album folder (and its base) up front."""
        for dest in dests:
            dest.base_dir.mkdir(parents=True, exist_ok=True)
            dest.meta_dir.mkdir(parents=True, exist_ok=True)
            dest.album_dir.mkdir(parents=True, exist_ok=True)

    def _encode_wav_to_destinations(
        self,
        wav_path: Path,
        dests: list[_EncodeDest],
        job: RipJob,
        *,
        track_number: int,
        embed_art: EmbedArtworkSet,
        disc_track_count: int,
        retitle: str | None = None,
        write_cover: bool = True,
        tag_as_image: bool = False,
        on_progress: Callable[[int, str, Path], None] | None = None,
        on_activity: Callable[[str], None] | None = None,
    ) -> list[Path]:
        """Encode *wav_path* to the destination(s); return output paths in order.

        Tags and optional embedded art are applied after each encode. Folder
        cover is written once per album directory when missing. When
        *tag_as_image* is True, tags are album-level (no track/ISRC).
        """
        # Compressed encodes first so a final WAV dest can rename the temp.
        ordered = [d for d in dests if d.format != 'wav'] + [d for d in dests if d.format == 'wav']
        outputs_by_dest: dict[int, Path] = {}
        n = max(1, len(ordered))
        covered: set[Path] = set()
        wav_left = sum(1 for d in ordered if d.format == 'wav')
        for i, dest in enumerate(ordered):
            dest.album_dir.mkdir(parents=True, exist_ok=True)
            dest.meta_dir.mkdir(parents=True, exist_ok=True)
            cover_dir = dest.meta_dir
            # Loose cover.jpg only beside lossless encodes; lossy keeps embeds only.
            if (
                write_cover
                and job.save_cover_file
                and is_lossless_format(dest.format)
                and cover_dir not in covered
                and (
                    job.folder_artwork is not None
                    or job.artwork is not None
                    or job.artwork_lossless is not None
                )
                and not (cover_dir / 'cover.jpg').is_file()
                and not (cover_dir / 'cover.png').is_file()
            ):
                _prepare_artwork(job, cover_dir)
                covered.add(cover_dir)

            if on_progress is not None:
                on_progress(i, dest.format, dest.out_path)

            if dest.format == 'wav':
                wav_left -= 1
                _place_wav_output(wav_path, dest.out_path, move=wav_left == 0)
            else:
                label = (
                    f'Encoding track {track_number} to {dest.format.upper()}…'
                    if track_number > 0
                    else f'Encoding to {dest.format.upper()}…'
                )

                def _enc_tick(*, _label=label) -> None:
                    if on_activity is not None:
                        on_activity(_label)

                with ProgressPulse(_enc_tick):
                    self._encode(dest.format, wav_path, dest.out_path, job)

            if job.album is not None:
                total_for_tags = len(job.album.tracks) if job.album.tracks else disc_track_count
                # Lyrics only for lossless (primary) encodes; mode picks tags / sidecar.
                lyrics_on = (
                    bool(job.embed_lyrics) and is_lossless_format(dest.format) and not tag_as_image
                )
                write_lyrics_tags = lyrics_on and lyrics_mode_writes_tags(job.lyrics_save_mode)
                write_lyrics_files = lyrics_on and lyrics_mode_writes_sidecar(job.lyrics_save_mode)
                self._tags.write_album_tags(
                    dest.out_path,
                    job.album,
                    track_number,
                    total_tracks=total_for_tags or None,
                    artist_style=job.artist_style,
                    image=tag_as_image,
                    encoded_by=encoder_tag_for_format(dest.format),
                    ripper_comment=(ripper_tag_value() if job.add_ripper_tag else None),
                    embed_lyrics=write_lyrics_tags,
                )
                if write_lyrics_files and track_number > 0:
                    write_lyrics_sidecar(
                        dest.out_path,
                        track_meta_for(job.album, track_number),
                    )
                if retitle and not tag_as_image:
                    _retag_title(dest.out_path, retitle, track_number=track_number)
            art = embed_art.for_format(dest.format, job)
            if art is not None:
                self._tags.embed_artwork(dest.out_path, art.data, art.mime)
            outputs_by_dest[id(dest)] = dest.out_path
            log.info(
                'Encoded %s → %s (%s/%s)',
                dest.format,
                dest.out_path,
                i + 1,
                n,
            )
        # Preserve caller destination order.
        return [outputs_by_dest[id(d)] for d in dests]

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
    ) -> Path | None:
        """Process every selected track with secure test & copy.

        Encode of track N overlaps extraction of track N+1 (drive-bound rip
        with CPU-bound encode). AccurateRip and CTDB share one PCM load.
        """
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
                    try:
                        job_pending.future.result(timeout=120)
                    except Exception:
                        pass
                    for dest in job_pending.dests:
                        try:
                            if dest.out_path.is_file():
                                # Incomplete encode after cancel — drop it.
                                if dest.out_path.stat().st_size < 1024:
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
                if job.test_and_copy and not extracted.matched:
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
        """Secure test&copy. Both passes use paranoia only; CRC retry, then per-track burst."""
        max_retries = max(0, int(job.test_copy_max_retries))
        attempts = 1 + max_retries if job.test_and_copy else 1

        final_mode = 'secure'
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
                    force_mode='secure' if job.test_and_copy else None,
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
                return 'burst (extract fallback, no test & copy)'
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
        log_entry.extract_mode = RipEngine._format_extract_mode(
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
          - ``None`` — secure first, optional ``-Z`` on extract failure
          - ``'burst'`` — burst only (used after test/copy CRC mismatch)
          - ``'secure'`` — secure only (no burst)
        """
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
                burst_fallback=burst_fallback and force_mode != 'secure',
                force_mode=force_mode,
                sample_offset=sample_offset,
                on_burst=on_burst,
                on_extract_progress=on_extract_progress,
            )
            if assert_expected:
                trim_wav_to_expected(wav_path, int(assert_expected))
            return mode, stats

        secure_timeout = _extract_timeout(expected_bytes, mode='secure')
        burst_timeout = _extract_timeout(expected_bytes, mode='burst')
        expected_frames = max(0, int(expected_sectors)) * FRAMES_PER_SECTOR

        def run_secure() -> ParanoiaStats:
            return self._run_cdparanoia(
                device,
                track_number,
                wav_path,
                mode='secure',
                timeout=secure_timeout,
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

        if force_mode == 'burst':
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
            burst_fallback=burst_fallback and force_mode != 'secure',
            run_secure=run_secure,
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
        disc = job.disc_info
        leadout = int(getattr(disc, 'leadout_lba', 0) or 0) if disc else 0
        if leadout <= 0 and disc is not None and disc.tracks:
            last = disc.tracks[-1]
            leadout = int(last.start_sector) + int(last.length_sectors)
        if on_progress is not None:
            on_progress(
                RipProgress(
                    track_number=0,
                    state=RipState.PREPARING,
                    fraction=0.01,
                    message='Probing drive overread…',
                )
            )
        try:
            job.overread = probe_overread(job.device, leadout_lba=leadout)
        except Exception as exc:
            log.debug('Overread probe failed: %s', exc)
            job.overread = OverreadCapability()

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
        if timeout_secure is None:
            timeout_secure = _extract_timeout(expected_bytes, mode='secure')
        if timeout_burst is None:
            timeout_burst = _extract_timeout(expected_bytes, mode='burst')
        expected_frames = max(0, int(end_sector) - int(start_sector)) * FRAMES_PER_SECTOR
        progress_bytes = (
            expected_bytes if progress_expected_bytes is None else progress_expected_bytes
        )

        def run_secure() -> ParanoiaStats:
            return self._run_cdparanoia_span(
                device,
                start_sector,
                end_sector,
                wav_path,
                mode='secure',
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

        if force_mode == 'burst':
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
            burst_fallback=burst_fallback and force_mode != 'secure',
            run_secure=run_secure,
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
        run_secure: Callable[[], ParanoiaStats],
        run_burst: Callable[[], ParanoiaStats],
        on_burst: Callable[[], None] | None,
        what: str,
    ) -> tuple[str, ParanoiaStats]:
        """Secure extract first; on incomplete/failure, optional ``-Z`` burst."""
        secure_err: str | None = None
        secure_stats = ParanoiaStats()

        # Prefer a complete secure WAV even when cdparanoia exits non-zero
        # (e.g. -X after exhausted retries — file may still be full length).
        try:
            secure_stats = run_secure()
            self._assert_wav_ok(
                wav_path,
                track_number,
                expected_bytes=expected_bytes,
                min_ratio=min_ratio,
            )
            # -X abort / hard skip with incomplete recovery: treat as failure
            # even when the file is large enough to pass the size check.
            if self._secure_pass_needs_burst(secure_stats, wav_path, expected_bytes):
                raise RuntimeError(
                    f'Secure pass aborted or incomplete after skips (exit {secure_stats.exit_code})'
                )
            return 'secure', secure_stats
        except Exception as exc:
            if self._cancelled:
                raise
            secure_err = str(exc)
            log.warning('Secure rip failed for %s: %s', what, secure_err)
            # Keep a full-size secure file if we already have one *and*
            # paranoia did not report an abort-with-skips situation.
            if not self._secure_pass_needs_burst(secure_stats, wav_path, expected_bytes):
                try:
                    self._assert_wav_ok(
                        wav_path,
                        track_number,
                        expected_bytes=expected_bytes,
                        min_ratio=min_ratio,
                    )
                    log.info(
                        '%s: accepting secure WAV despite cdparanoia error',
                        what,
                    )
                    return 'secure', secure_stats
                except Exception:
                    pass

        if not burst_fallback:
            raise RuntimeError(f'Secure rip failed for {what}: {secure_err}')

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
                f'Secure and burst rip both failed for {what}. Secure: {secure_err}; burst: {exc}'
            ) from exc

        log.info('%s recovered via burst mode (-Z)', what)
        combined = secure_stats.merge(burst_stats) if secure_stats.reads else burst_stats
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
        album_dir, _ = build_output_paths(
            base_dir=base,
            album_folder_template=job.album_folder_template,
            filename_template=job.filename_template,
            album=job.album,
            track_number=1,
            extension=ext,
            artist_style=job.artist_style,
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
                report(RipState.RIPPING, 0.15, 'Extracting disc image…')
                try:
                    mode, img_stats = _img_span(test_wav, on_extract_progress=_img_extract_progress)
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
                    notes.append(
                        'Disc image extracted in burst mode '
                        '(secure mode failed; disc may be scratched)'
                    )

                use_wav = test_wav
                # Burst-recovered first pass skips test&copy CRC theater.
                if job.test_and_copy and not image_burst_recovered and not self._cancelled:
                    max_img_retries = max(0, int(job.test_copy_max_retries))
                    img_attempts = 1 + max_img_retries
                    img_matched = False
                    test_crc = ''
                    copy_crc = ''
                    copy_mode = mode
                    for img_attempt in range(img_attempts):
                        if self._cancelled:
                            break
                        # Phase marks for retries (fill jumps at boundaries).
                        extract_base = 0.05 if img_attempt == 0 else 0.66
                        copy_base = 0.32 if img_attempt == 0 else 0.75
                        if img_attempt > 0:
                            report(
                                RipState.RIPPING,
                                extract_base,
                                f'Retrying disc image test & copy (attempt {img_attempt + 1})…',
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

                            mode, retry_stats = _img_span(
                                test_wav,
                                on_extract_progress=_img_retry_progress,
                            )
                            img_stats.merge(retry_stats)
                            report(
                                RipState.RIPPING,
                                last_overall,
                                'Trimming disc image…',
                            )
                            trim_wav_to_expected(test_wav, expected_bytes)

                        report(
                            RipState.RIPPING,
                            max(last_overall, copy_base - 0.02),
                            'Checking image CRC…',
                        )

                        def _img_test_crc_tick() -> None:
                            report(
                                RipState.RIPPING,
                                last_overall,
                                'Checking image CRC…',
                                nudge=True,
                            )

                        with ProgressPulse(_img_test_crc_tick):
                            test_crc = wav_pcm_crc(test_wav)
                        if job.defeat_audio_cache:
                            report(
                                RipState.RIPPING,
                                max(last_overall, copy_base - 0.01),
                                'Defeating drive cache…',
                            )
                            flush_drive_cache(
                                job.device,
                                disc,
                                avoid_start=file_start,
                                avoid_end=end_sector,
                            )
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
                        copy_mode, copy_stats = _img_span(
                            copy_wav, on_extract_progress=_img_copy_progress
                        )
                        img_stats.merge(copy_stats)
                        report(
                            RipState.RIPPING,
                            0.55,
                            'Disc image copy complete…',
                        )
                        trim_wav_to_expected(copy_wav, expected_bytes)
                        report(
                            RipState.RIPPING,
                            0.58,
                            'Checking image CRC…',
                        )

                        def _img_copy_crc_tick() -> None:
                            report(
                                RipState.RIPPING,
                                last_overall,
                                'Checking image CRC…',
                                nudge=True,
                            )

                        with ProgressPulse(_img_copy_crc_tick):
                            copy_crc = wav_pcm_crc(copy_wav)
                        if test_crc and copy_crc and test_crc == copy_crc:
                            img_matched = True
                            break
                        notes.append(
                            f'Image test/copy CRC mismatch '
                            f'({test_crc} ≠ {copy_crc})'
                            + ('; retrying' if img_attempt + 1 < img_attempts else '')
                        )

                    if not img_matched:
                        if job.burst_fallback and not self._cancelled:
                            report(
                                RipState.RIPPING,
                                max(last_overall, 0.84),
                                'Image test & copy failed — burst recovery…',
                            )
                            flush_drive_cache(
                                job.device,
                                disc,
                                avoid_start=file_start,
                                avoid_end=end_sector,
                            )

                            def _img_burst_progress(local: float) -> None:
                                local_c = max(0.0, min(1.0, local))
                                report(
                                    RipState.RIPPING,
                                    max(last_overall, 0.84 + 0.10 * local_c),
                                    'Burst recovery for disc image…',
                                )

                            copy_mode, burst_stats = _img_span(
                                copy_wav,
                                force_mode='burst',
                                on_extract_progress=_img_burst_progress,
                            )
                            img_stats.merge(burst_stats)
                            report(
                                RipState.RIPPING,
                                last_overall,
                                'Trimming disc image…',
                            )
                            trim_wav_to_expected(copy_wav, expected_bytes)
                            mode = 'burst'
                            notes.append(
                                f'Image test/copy CRC mismatch '
                                f'({test_crc} ≠ {copy_crc}); recovered in burst'
                            )
                        else:
                            return RipResult(
                                success=False,
                                error=(
                                    f'Image test/copy CRC mismatch '
                                    f'({test_crc} ≠ {copy_crc}); '
                                    f'secure retries exhausted'
                                ),
                                notes=notes,
                                album_dir=album_dir,
                            )
                    if copy_mode == 'burst':
                        mode = 'burst'
                        notes.append('Disc image used burst mode')
                    self._unlink_quiet(test_wav)
                    use_wav = copy_wav

                for line in img_stats.summary_lines(length_sectors=total_sectors):
                    notes.append(line)
                if mode == 'burst':
                    notes.append('Burst fallback (-Z) used for disc image')
                if rip_log is not None:
                    for line in img_stats.summary_lines(length_sectors=total_sectors):
                        rip_log.notes.append(f'Image: {line}')
                    if mode == 'burst':
                        rip_log.notes.append('Image: secure paranoia failed — burst (-Z) used')

                if self._cancelled:
                    return RipResult(
                        success=False, cancelled=True, notes=notes, album_dir=album_dir
                    )

                ar_results: list[AccurateRipResult] = []
                ctdb_results: list[CtdbResult] = []
                burst_tracks: list[int] = [0] if mode == 'burst' else []
                image_test_copy_ok = bool(job.test_and_copy and mode != 'burst')

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
                    return RipResult(
                        success=False,
                        cancelled=True,
                        notes=notes,
                        album_dir=album_dir,
                        accuraterip=ar_results,
                        ctdb=ctdb_results,
                        burst_tracks=burst_tracks,
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
                    return RipResult(
                        success=False,
                        error=str(enc_exc),
                        notes=notes,
                        album_dir=dests[0].meta_dir if dests else album_dir,
                    )

                for dest, this_out in zip(dests, output_files, strict=True):
                    if not this_out.is_file() or this_out.stat().st_size < 1000:
                        return RipResult(
                            success=False,
                            error='Image encode produced no output',
                            notes=notes,
                            album_dir=dest.meta_dir,
                        )

                album_dirs = _unique_album_dirs(output_files, dests[0].meta_dir)
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
                        and p.suffix.lower() in {'.flac', '.wav', '.aiff', '.aif'}
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
                        test_and_copy=job.test_and_copy,
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

                log_path = None
                if rip_log is not None:
                    try:
                        rip_log.finish()
                        # Always beside the first (primary) encode.
                        log_path = rip_log.write(primary_album)
                        notes.append(f'Rip log written to {log_path}')
                    except Exception as exc:
                        notes.append(f'Rip log warning: {exc}')

                done_label = primary_out.name if primary_out else 'image'
                if job.write_cue_file and primary_out is not None:
                    done_label = f'{primary_out.name} + {basename}.cue'
                if len(dests) > 1:
                    done_label = f'{done_label} · {len(dests)} encodes'
                if ar_results:
                    ar_ok = sum(
                        1 for r in ar_results if r.confidence == AccurateRipConfidence.MATCH
                    )
                    done_label = f'{done_label} · AR {ar_ok}/{len(ar_results)}'
                if ctdb_results:
                    ctdb_ok = sum(1 for r in ctdb_results if r.confidence == CtdbConfidence.MATCH)
                    done_label = f'{done_label} · CTDB {ctdb_ok}/{len(ctdb_results)}'
                report(
                    RipState.DONE,
                    1.0,
                    f'Copy Image complete → {done_label}',
                )
                return RipResult(
                    success=True,
                    output_files=output_files,
                    album_dir=primary_album,
                    notes=notes,
                    log_path=log_path,
                    cover_path=cover_path,
                    htoa_ripped=htoa_in_image,
                    accuraterip=ar_results,
                    ctdb=ctdb_results,
                    burst_tracks=burst_tracks,
                )
        except Exception as exc:
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
        """Build shared cdparanoia argv for secure or burst extract."""
        cmd = [binary, '-w', '-d', device]
        if sample_offset:
            # Apply drive sample offset at read time (read offset correction).
            cmd.extend(['-O', str(int(sample_offset))])
        if mode == 'burst':
            cmd.extend(burst_paranoia_flags())
        else:
            cmd.extend(secure_paranoia_flags())
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
        on_extract_progress: Callable[[float], None] | None = None,
        expected_frames: int = 0,
        expected_bytes: int | None = None,
    ) -> ParanoiaStats:
        binary = find_cdparanoia()
        if not binary:
            raise RuntimeError('cdparanoia not found')
        cmd = self._paranoia_cmd_base(binary, device, mode=mode, sample_offset=sample_offset)
        cmd.extend([str(track_number), str(wav_path)])
        return self._run_paranoia(
            cmd,
            what=f'cdparanoia {mode} track {track_number}',
            timeout=timeout,
            allow_nonzero=allow_nonzero,
            on_extract_progress=on_extract_progress,
            expected_frames=expected_frames,
            wav_path=wav_path,
            expected_bytes=expected_bytes,
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
        on_extract_progress: Callable[[float], None] | None = None,
        expected_frames: int = 0,
        expected_bytes: int | None = None,
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
            raise RuntimeError(f'Invalid sector span [{start}, {end}) for disc image')
        # N = end - start sectors → last inclusive = end - 1 → relative B.
        rel_end = end - start - 1
        span = f'[.{start}]-[.{rel_end}]'
        cmd = self._paranoia_cmd_base(binary, device, mode=mode, sample_offset=sample_offset)
        cmd.extend([span, str(wav_path)])
        return self._run_paranoia(
            cmd,
            what=f'cdparanoia {mode} span {span} (sectors [{start}, {end}))',
            timeout=timeout,
            allow_nonzero=allow_nonzero,
            on_extract_progress=on_extract_progress,
            expected_frames=expected_frames,
            wav_path=wav_path,
            expected_bytes=expected_bytes,
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
            raise RuntimeError(f'{label} extraction produced no file for track {track_number}')
        size = wav_path.stat().st_size
        if size < 1000:
            raise RuntimeError(f'{label} extraction produced empty data for track {track_number}')
        if expected_bytes and expected_bytes > 0:
            minimum = wav_size_minimum(expected_bytes, min_ratio)
            if size < minimum:
                got_pct = (100.0 * size / expected_bytes) if expected_bytes else 0.0
                raise RuntimeError(
                    f'{label} extraction incomplete for track {track_number}: '
                    f'{size} bytes < {minimum} required '
                    f'(got {got_pct:.1f}% of {expected_bytes} CDDA bytes; '
                    f'need ≥{min_ratio:.1%} within one-sector slack)'
                )

    def _encode(self, fmt: str, wav_path: Path, out_path: Path, job: RipJob) -> None:
        """Encode with stock encoder presets; PCM is not pre-processed."""
        if out_path.exists():
            out_path.unlink()

        if fmt == 'flac':
            self._run(
                flac_encode_argv(wav_path, out_path, job.flac_compression),
                what='flac',
            )
        elif fmt == 'mp3':
            self._run(
                mp3_encode_argv(wav_path, out_path, job.mp3_quality),
                what='lame',
            )
        elif fmt == 'aac':
            quality = job.aac_quality or (
                f'cbr{int(job.aac_bitrate)}' if job.aac_bitrate else 'cbr256'
            )
            self._run(
                aac_encode_argv(wav_path, out_path, quality),
                what='ffmpeg aac',
            )
        elif fmt == 'qaac':
            self._run(
                qaac_encode_argv(
                    wav_path,
                    out_path,
                    job.qaac_quality or 'cvbr256',
                ),
                what='qaac',
            )
        elif fmt == 'opus':
            if shutil.which('opusenc'):
                self._run(
                    opus_encode_argv(wav_path, out_path, job.opus_bitrate),
                    what='opusenc',
                )
            else:
                self._run(
                    opus_ffmpeg_argv(wav_path, out_path, job.opus_bitrate),
                    what='ffmpeg opus',
                )
        elif fmt == 'aiff':
            self._run(
                aiff_encode_argv(wav_path, out_path),
                what='ffmpeg aiff',
            )
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
        on_extract_progress: Callable[[float], None] | None = None,
        expected_frames: int = 0,
        wav_path: Path | None = None,
        expected_bytes: int | None = None,
    ) -> ParanoiaStats:
        """Run cdparanoia, stream ``-e`` progress, return :class:`ParanoiaStats`."""

        def register(proc: subprocess.Popen) -> None:
            with self._proc_lock:
                self._procs.add(proc)

        def unregister(proc: subprocess.Popen) -> None:
            with self._proc_lock:
                self._procs.discard(proc)

        return run_paranoia_streaming(
            cmd,
            what=what,
            timeout=timeout,
            allow_nonzero=allow_nonzero,
            on_extract_progress=on_extract_progress,
            expected_frames=expected_frames,
            wav_path=wav_path,
            expected_bytes=expected_bytes,
            is_cancelled=lambda: self._cancelled,
            register_proc=register,
            unregister_proc=unregister,
        )

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
        proc: subprocess.Popen[str] | None = None
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            with self._proc_lock:
                self._procs.add(proc)
            try:
                _stdout, stderr = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.communicate(timeout=5)
                except Exception:
                    pass
                raise RuntimeError(f'{what} timed out after {timeout}s') from None
            code = proc.returncode if proc is not None else -1
            return code if code is not None else -1, stderr or ''
        except OSError as exc:
            raise RuntimeError(f'Failed to run {what}: {exc}') from exc
        finally:
            if proc is not None:
                with self._proc_lock:
                    self._procs.discard(proc)

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

        report(RipState.VERIFYING, 0.70, 'Loading disc image for verification…')

        def _img_load_tick() -> None:
            report(
                RipState.VERIFYING,
                0.70,
                'Loading disc image for verification…',
                nudge=True,
            )

        try:
            with ProgressPulse(_img_load_tick):
                image_pcm = load_cdda_pcm(wav_path)
        except Exception as exc:
            msg = f'Image PCM load failed: {exc}'
            log.warning('%s', msg)
            notes.append(msg)
            return

        mode_label = f'{extract_mode} (copy image)'
        image_quality: float | None = None
        image_had_errors = False
        if image_stats is not None:
            total_secs = sum(int(t.length_sectors) for t in disc.tracks)
            image_quality = image_stats.quality_percent(total_secs)
            image_had_errors = bool(image_stats.had_errors) or extract_mode == 'burst'
        n = max(1, len(disc.tracks))
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
            pcm = _image_track_pcm(
                image_pcm,
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
                    log_entry.notes.append(msg)
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


def _place_wav_output(src: Path, dest: Path, *, move: bool) -> None:
    """Write *src* to *dest*; rename when safe, else copy."""
    if dest.exists():
        dest.unlink()
    if move:
        try:
            src.replace(dest)
            return
        except OSError:
            log.debug('WAV rename failed (%s → %s); copying', src, dest)
    shutil.copy2(src, dest)


def _scaled_embed(source: ArtworkImage | None, max_edge: int) -> ArtworkImage | None:
    """Return *source* scaled so the longest edge is at most *max_edge*."""
    if source is None:
        return None
    if max_edge > 0 and source.max_edge > max_edge:
        return ArtworkFetcher().resize(source, max_edge)
    return source


def _prepare_artwork(
    job: RipJob,
    album_dir: Path,
) -> tuple[EmbedArtworkSet, ArtworkImage | None, Path | None, list[str]]:
    """Save full-size cover to the album folder; return lossy/lossless embeds.

    Called **before** track extraction begins.
    """
    notes: list[str] = []
    candidates = [
        img for img in (job.folder_artwork, job.artwork, job.artwork_lossless) if img is not None
    ]
    folder_art = max(candidates, key=lambda img: img.max_edge) if candidates else None

    cover_path: Path | None = None
    embeds = EmbedArtworkSet()
    if job.save_cover_file and folder_art is not None and folder_art.data:
        if folder_art.mime == 'image/png':
            cover_path = album_dir / 'cover.png'
        else:
            cover_path = album_dir / 'cover.jpg'
            # Re-encode non-JPEG as JPEG for a consistent cover.jpg when needed.
            if folder_art.mime not in ('image/jpeg', 'image/jpg') and cover_path.suffix == '.jpg':
                try:
                    import io

                    from PIL import Image  # type: ignore[import-not-found]

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
                        notes.append(f'Folder cover written ({im.size[0]}×{im.size[1]})')
                        embeds = _embed_set_from_job(job, folder_art)
                        return embeds, folder_art, cover_path, notes
                except ImportError:
                    # Pillow not installed — fall through and write raw bytes.
                    pass
                except Exception:
                    cover_path = album_dir / 'cover.png'
        try:
            cover_path.write_bytes(folder_art.data)
            notes.append(
                f'Folder cover written ({folder_art.width}×{folder_art.height} {folder_art.source})'
            )
        except OSError as exc:
            log.warning('Could not write cover file: %s', exc)
            notes.append(f'Could not write cover: {exc}')
            cover_path = None

    embeds = _embed_set_from_job(job, folder_art)
    if embeds.lossy is not None:
        notes.append(f'Lossy embed artwork ready ({embeds.lossy.width}×{embeds.lossy.height})')
    if embeds.lossless is not None:
        notes.append(
            f'Lossless embed artwork ready ({embeds.lossless.width}×{embeds.lossless.height})'
        )

    return embeds, folder_art, cover_path, notes


def _embed_set_from_job(job: RipJob, folder_art: ArtworkImage | None) -> EmbedArtworkSet:
    """Build lossy/lossless embed images from job fields + folder source."""
    lossy: ArtworkImage | None = None
    lossless: ArtworkImage | None = None
    if job.embed_artwork:
        lossy = _scaled_embed(
            job.artwork or folder_art,
            int(job.artwork_max_size),
        )
    if job.embed_artwork_lossless:
        lossless = _scaled_embed(
            job.artwork_lossless or folder_art,
            int(job.artwork_max_size_lossless),
        )
    return EmbedArtworkSet(lossy=lossy, lossless=lossless)


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
            tags = audio.tags
            if tags is None:
                return
            tags.add(TIT2(encoding=3, text=title))
            tags.add(TRCK(encoding=3, text=str(track_number)))
            audio.save()
        elif suffix in {'.m4a', '.mp4', '.aac'}:
            from mutagen.mp4 import MP4

            audio = MP4(path)
            audio['\xa9nam'] = [title]
            audio['trkn'] = [(track_number, 0)]
            audio.save()
        elif suffix in {'.opus', '.ogg'}:
            from mutagen.oggopus import OggOpus
            from mutagen.oggvorbis import OggVorbis

            try:
                audio = OggOpus(path)
            except Exception:
                audio = OggVorbis(path)
            audio['TITLE'] = [title]
            audio['TRACKNUMBER'] = [str(track_number)]
            audio.save()
    except Exception as exc:
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


def _track_avoid_span(disc: DiscInfo | None, track_no: int) -> tuple[int | None, int | None]:
    """Absolute ``[start, end)`` for the track just extracted (cache flush)."""
    if disc is None:
        return None, None
    for t in disc.tracks:
        if t.number == track_no:
            start = int(t.start_sector)
            return start, start + int(t.length_sectors)
    return None, None


def _job_per_track_overread_span(job: RipJob | None, track_no: int) -> tuple[int, int, int] | None:
    """Job-aware wrapper around :func:`per_track_overread_read_span`."""
    if job is None:
        return None
    disc = job.disc_info
    if disc is None or not disc.tracks:
        return None
    toc = None
    for t in disc.tracks:
        if t.number == track_no:
            toc = t
            break
    if toc is None:
        return None
    return per_track_overread_read_span(
        track_no=track_no,
        track_start=int(toc.start_sector),
        track_length=int(toc.length_sectors),
        first_track_no=int(disc.tracks[0].number),
        last_track_no=int(disc.tracks[-1].number),
        sample_offset=job.sample_offset,
        overread=job.overread,
    )


def _unique_album_dirs(outputs: list[Path], album_dir: Path | None) -> list[Path]:
    """Album folders that should receive data-track .bin copies."""
    dirs: list[Path] = []
    seen: set[Path] = set()
    if album_dir is not None:
        dirs.append(album_dir)
        seen.add(album_dir)
    skip = {'.cue', '.log', '.jpg', '.jpeg', '.png', '.bin'}
    for path in outputs:
        if path.suffix.lower() in skip:
            continue
        parent = path.parent
        if parent in seen:
            continue
        seen.add(parent)
        dirs.append(parent)
    return dirs


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
    test_copy_mismatches: list[int] | None = None,
    log_dir: Path | None = None,
    ctdb_results: list[CtdbResult] | None = None,
) -> RipResult:
    """Build a RipResult; write the rip log beside the *first* encode only.

    *log_dir* / *album_dir* should be the primary encode album folder. Falls
    back to the first output's parent when needed.
    """
    log_path: Path | None = None
    if rip_log is not None:
        rip_log.notes.extend(n for n in notes if n not in rip_log.notes)
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
    )


def _image_track_pcm(
    image_pcm: bytes,
    *,
    start_sector: int,
    length_sectors: int,
    file_start: int,
) -> bytes:
    """Slice one TOC track's CDDA bytes out of a continuous image PCM blob."""
    rel = max(0, int(start_sector) - int(file_start))
    start = rel * 2352
    length = max(0, int(length_sectors)) * 2352
    if length <= 0 or start >= len(image_pcm):
        return b''
    return image_pcm[start : min(start + length, len(image_pcm))]


def _summarize_ar(results: list[AccurateRipResult]) -> str:
    if not results:
        return ''
    matches = sum(1 for r in results if r.confidence == AccurateRipConfidence.MATCH)
    mismatches = sum(1 for r in results if r.confidence == AccurateRipConfidence.MISMATCH)
    missing = sum(1 for r in results if r.confidence == AccurateRipConfidence.NOT_IN_DB)
    parts = [f'AR {matches}/{len(results)} match']
    if mismatches:
        parts.append(f'{mismatches} mismatch')
    if missing:
        parts.append(f'{missing} not in DB')
    return ', '.join(parts)


def _summarize_ctdb(results: list[CtdbResult]) -> str:
    if not results:
        return ''
    matches = sum(1 for r in results if r.confidence == CtdbConfidence.MATCH)
    mismatches = sum(1 for r in results if r.confidence == CtdbConfidence.MISMATCH)
    missing = sum(1 for r in results if r.confidence == CtdbConfidence.NOT_IN_DB)
    parts = [f'CTDB {matches}/{len(results)} match']
    if mismatches:
        parts.append(f'{mismatches} mismatch')
    if missing:
        parts.append(f'{missing} not in DB')
    return ', '.join(parts)
