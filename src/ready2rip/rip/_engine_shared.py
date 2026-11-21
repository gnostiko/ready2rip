# SPDX-License-Identifier: GPL-3.0-or-later
"""Import namespace for RipEngine mixin modules.

Star-imported by engine_* mixin modules. Unused imports are intentional
(mixin method globals). See per-file-ignores in pyproject.toml.
"""

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
from dataclasses import dataclass, field, replace
from enum import Enum, auto
from pathlib import Path

from ready2rip.accuraterip import (
    AccurateRipConfidence,
    AccurateRipResult,
    AccurateRipVerifier,
)
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
from ready2rip.disc.drive_info import probe_drive
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
from ready2rip.metadata.models import AlbumMetadata
from ready2rip.paths import (
    DEFAULT_ALBUM_FOLDER_TEMPLATE,
    DEFAULT_FILENAME_TEMPLATE,
    build_output_paths,
    track_meta_for,
    unique_output_path,
)
from ready2rip.replaygain import apply_replaygain
from ready2rip.rip.artwork_prep import prepare_artwork as _prepare_artwork
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
    alac_encode_argv,
    encoder_tag_for_format,
    flac_encode_argv,
    mp3_encode_argv,
    opus_encode_argv,
    opus_ffmpeg_argv,
    qaac_available,
    qaac_encode_argv,
    wavpack_encode_argv,
    wavpack_ffmpeg_argv,
)
from ready2rip.rip.encode_dest import (
    audio_dir_for_format,
    encode_format_list,
    format_folder_name,
    is_lossless_extension,
    is_lossless_format,
    is_lossy_format,
    should_embed_artwork_for_format,
)
from ready2rip.rip.engine_types import *
from ready2rip.rip.job_extract import job_extract_mode, sync_job_extract_flags
from ready2rip.rip.outcome import (
    apply_burst_notes,
    archival_fail_reasons,
    compute_tiers,
    finish_result,
    format_done_message,
    summarize_ar,
    summarize_ctdb,
)
from ready2rip.rip.paranoia_run import run_paranoia_streaming
from ready2rip.rip.paranoia_stats import FRAMES_PER_SECTOR, ParanoiaStats
from ready2rip.rip.plan import (
    ENCODE_EXTENSIONS,
    EncodeDest,
    apply_existing_files_policy,
    place_wav_output,
    unique_album_dirs,
)
from ready2rip.rip.plan import (
    conflicting_paths as plan_conflicting_paths,
)
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
from ready2rip.settings_defs import (
    DEFAULT_EXISTING_FILES_POLICY,
    lyrics_mode_writes_sidecar,
    lyrics_mode_writes_tags,
    normalize_existing_files_policy,
)
from ready2rip.tags.artists import DEFAULT_ARTIST_STYLE, artist_style_for_container
from ready2rip.tags.writer import TagWriter, ripper_tag_value
from ready2rip.util import find_cdparanoia, subprocess_env_for, validate_device_path

log = logging.getLogger('ready2rip.rip.engine')

from ready2rip.rip.engine_helpers import (
    _expected_wav_sizes,
    _extract_timeout,
    _job_per_track_overread_span,
    _retag_title,
    _track_avoid_span,
)
