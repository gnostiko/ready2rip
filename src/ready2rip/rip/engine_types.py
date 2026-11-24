# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared rip engine types."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path

from ready2rip.accuraterip import (
    AccurateRipResult,
)
from ready2rip.artwork.fetch import ArtworkImage
from ready2rip.ctdb import (
    CtdbResult,
)
from ready2rip.disc.overread import (
    OverreadCapability,
)
from ready2rip.disc.probe import DiscInfo
from ready2rip.metadata.models import AlbumMetadata
from ready2rip.paths import (
    DEFAULT_ALBUM_FOLDER_TEMPLATE,
    DEFAULT_FILENAME_TEMPLATE,
)
from ready2rip.rip.encode_dest import (
    is_lossless_format,
    is_lossy_format,
)
from ready2rip.rip.paranoia_stats import ParanoiaStats
from ready2rip.rip.plan import (
    EncodeDest,
)
from ready2rip.rip.riplog import (
    TrackLogEntry,
)
from ready2rip.rip.secure import (
    SECURE_MIN_WAV_RATIO,
    SECURE_TEST_COPY_RETRIES,
)
from ready2rip.settings_defs import (
    DEFAULT_EXISTING_FILES_POLICY,
)
from ready2rip.tags.artists import DEFAULT_ARTIST_STYLE


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
    # Tiered outcome: secure CRC ≠ online DB confirm ≠ archival pass.
    secure_ok: bool = True
    db_verified: bool = False
    archival_ok: bool = True
    outcome_summary: str = ''


@dataclass
class RipJob:
    """Description of a full-disc or multi-track rip."""

    device: str
    track_numbers: list[int]
    output_directory: Path
    encode_format: str = 'flac'  # flac | wav | aiff | wavpack | alac (+ lossy via also-encode)
    flac_compression: int = 5
    wavpack_compression: str = 'high'  # fast | normal | high | veryhigh | extra
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
    # Force "Primary feat. Guests" on M4A/AAC/ALAC/QAAC for Apple/iPod.
    m4a_feat_artists: bool = True
    save_cover_file: bool = True
    verify_accuraterip: bool = True
    verify_ctdb: bool = True
    submit_ctdb: bool = False
    # When AccurateRip is on, fail archival if zero tracks MATCH (strict).
    require_db_match: bool = False
    # When True, AccurateRip/CTDB mismatches fail archival (default: soft).
    archival_strict: bool = False
    sample_offset: int = 0
    # True after Detect or manual save in drive setup (offset may still be 0).
    offset_configured: bool = False
    # Drive can READ CD into lead-in / lead-out (for offset edge samples).
    overread: OverreadCapability = field(default_factory=OverreadCapability)
    disc_info: DiscInfo | None = None
    # Total tracks on the disc (for AR first/last sample skip), not just selected.
    disc_track_count: int = 0
    # archival | normal | burst — drives extract flags below.
    rip_mode: str = 'archival'
    # Full-rip -Z (paranoia off). Skips secure passes and test & copy.
    burst_mode: bool = False
    # Optional insecure -Z recovery after secure failure (off by default).
    # Ignored when burst_mode is True / rip_mode is burst.
    burst_fallback: bool = False
    # Expected minimum WAV size as a fraction of ideal CDDA size (near complete).
    min_wav_size_ratio: float = SECURE_MIN_WAV_RATIO
    # Write a detailed status log next to the ripped files.
    write_rip_log: bool = True
    # Write multi-file (or image) CUE sheet after a secure rip (lossless only).
    write_cue_file: bool = True
    # Write ready2rip + version into Encoded by (Ripper tag preference).
    # Encoder / ENCODER / TSSE / ©too always records the codec tool + version.
    add_ripper_tag: bool = True
    # Save lyrics with lossless encodes (master); mode chooses tags / sidecar / both.
    embed_lyrics: bool = True
    lyrics_save_mode: str = 'both'
    # Double-rip each track and require matching CRCs (test & copy).
    # Ignored when burst_mode is True (single -Z pass).
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
    # overwrite | ask | skip | version — ask is resolved in the UI before run.
    existing_files: str = DEFAULT_EXISTING_FILES_POLICY
    # Longest edge for embeds when preparing from folder art.
    artwork_max_size: int = 600  # lossy embed edge; match settings default
    artwork_max_size_lossless: int = 600


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
    dests: list[EncodeDest]
    extracted: _TrackExtract
    test_wav: Path
    copy_wav: Path
    log_entry: TrackLogEntry | None


class _CrcMismatch(Exception):
    """Secure test&copy failed after retries with no recovered copy WAV."""

    def __init__(self, track_number: int, message: str) -> None:
        super().__init__(message)
        self.track_number = track_number
        self.message = message



