# SPDX-License-Identifier: GPL-3.0-or-later
"""Build a RipJob from AppSettings + session context (gi-free)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ready2rip.rip.encode_dest import normalize_encode_format
from ready2rip.rip.engine_types import RipJob
from ready2rip.settings_defs import (
    derive_extract_flags,
    normalize_also_encode_folder,
    normalize_also_encode_format,
    normalize_existing_files_policy,
    normalize_lyrics_save_mode,
    normalize_qaac_quality,
    normalize_rip_mode,
    normalize_wavpack_level,
)
from ready2rip.util import validate_device_path

if TYPE_CHECKING:
    from ready2rip.artwork.fetch import ArtworkImage
    from ready2rip.disc.probe import DiscInfo
    from ready2rip.metadata.models import AlbumMetadata


def job_from_settings(
    settings: Any,
    *,
    track_numbers: list[int],
    device: str | None = None,
    disc: DiscInfo | None = None,
    album: AlbumMetadata | None = None,
    folder_artwork: ArtworkImage | None = None,
    artwork_embed: ArtworkImage | None = None,
    artwork_embed_lossless: ArtworkImage | None = None,
) -> RipJob:
    """Map current preferences + UI session state into a RipJob."""
    resolved_device = validate_device_path(
        device or settings.device or '/dev/sr0'
    )
    also_folder = normalize_also_encode_folder(settings.also_encode_folder)
    mode = normalize_rip_mode(settings.rip_mode)
    _mode, burst, test_and_copy = derive_extract_flags(mode)
    folder_art = folder_artwork
    return RipJob(
        device=resolved_device,
        track_numbers=list(track_numbers),
        output_directory=settings.resolved_output_directory(),
        encode_format=normalize_encode_format(settings.encode_format),
        flac_compression=settings.flac_compression,
        wavpack_compression=normalize_wavpack_level(settings.wavpack_compression),
        mp3_quality=settings.mp3_quality,
        aac_quality=settings.aac_quality,
        aac_bitrate=settings.aac_bitrate,
        qaac_quality=normalize_qaac_quality(settings.qaac_quality),
        opus_bitrate=settings.opus_bitrate,
        also_encode=bool(settings.also_encode),
        also_encode_format=normalize_also_encode_format(settings.also_encode_format),
        also_encode_folder=also_folder,
        also_encode_directory=(
            str(settings.resolved_also_encode_directory())
            if also_folder == 'custom_folder'
            else ''
        ),
        apply_replaygain=settings.apply_replaygain,
        write_sound_check=settings.write_sound_check,
        add_ripper_tag=settings.add_ripper_tag,
        embed_lyrics=settings.embed_lyrics,
        lyrics_save_mode=normalize_lyrics_save_mode(settings.lyrics_save_mode),
        embed_artwork=settings.embed_artwork,
        embed_artwork_lossless=settings.embed_artwork_lossless,
        artwork=((artwork_embed or folder_art) if settings.embed_artwork else None),
        artwork_lossless=(
            (artwork_embed_lossless or folder_art)
            if settings.embed_artwork_lossless
            else None
        ),
        folder_artwork=folder_art,
        album=album,
        filename_template=settings.filename_template,
        album_folder_template=settings.album_folder_template,
        artist_style=settings.artist_style,
        m4a_feat_artists=settings.m4a_feat_artists,
        save_cover_file=folder_art is not None,
        verify_accuraterip=settings.verify_accuraterip,
        verify_ctdb=settings.verify_ctdb,
        submit_ctdb=settings.submit_ctdb,
        require_db_match=settings.require_db_match,
        archival_strict=settings.archival_strict,
        sample_offset=settings.drive_sample_offset,
        offset_configured=bool(settings.drive_offset_configured),
        disc_info=disc,
        disc_track_count=disc.track_count if disc is not None else 0,
        rip_mode=mode,
        burst_mode=burst,
        burst_fallback=settings.burst_fallback and mode == 'archival',
        write_rip_log=settings.write_rip_log,
        write_cue_file=settings.write_cue_file,
        test_and_copy=test_and_copy,
        rip_htoa=settings.rip_htoa,
        rip_data_tracks=settings.rip_data_tracks,
        copy_image=settings.copy_image,
        artwork_max_size=settings.artwork_max_size,
        artwork_max_size_lossless=settings.artwork_max_size_lossless,
        existing_files=normalize_existing_files_policy(settings.existing_files),
    )
