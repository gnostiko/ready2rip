# SPDX-License-Identifier: GPL-3.0-or-later
"""Sync preference widgets from SettingsStore (split from the main window)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ready2rip.paths import (
    DEFAULT_ALBUM_FOLDER_TEMPLATE,
    DEFAULT_FILENAME_TEMPLATE,
    FILENAME_SCHEMES,
    FOLDER_SCHEMES,
)
from ready2rip.settings import (
    ENCODERS,
    default_output_directory,
    normalize_also_encode_folder,
    normalize_also_encode_format,
    normalize_existing_files_policy,
    normalize_lyrics_save_mode,
)
from ready2rip.tags.artists import ARTIST_STYLES, normalize_artist_style
from ready2rip.ui.prefs_common import block_handlers

if TYPE_CHECKING:
    from ready2rip.window import Ready2RipWindow


def sync_options_from_store(window: Ready2RipWindow) -> None:
    """Refresh settings widgets from store (e.g. after Drive setup)."""
    settings = window.store.get()
    ids = [e[0] for e in ENCODERS]
    default_out = default_output_directory()

    handlers = [
        (window._output_row, window._on_output_changed),
        (window._existing_files_row, window._on_existing_files_changed),
        (window._device_row, window._on_device_path_changed),
        (window._folder_scheme_row, window._on_folder_scheme_changed),
        (window._folder_template_row, window._on_folder_template_changed),
        (window._filename_scheme_row, window._on_filename_scheme_changed),
        (window._filename_row, window._on_filename_changed),
        (window._artist_style_row, window._on_artist_style_changed),
        (window._m4a_feat_row, window._on_m4a_feat_toggled),
        (window._encoder_row, window._on_encoder_changed),
        (window._quality_row, window._on_quality_changed),
        (window._also_encode_row, window._on_also_encode_toggled),
        (window._also_format_row, window._on_also_format_changed),
        (window._also_quality_row, window._on_also_quality_changed),
        (window._also_folder_row, window._on_also_folder_changed),
        (window._also_directory_row, window._on_also_directory_changed),
        (window._offset_row, window._on_offset_changed),
        (window._rip_mode_row, window._on_rip_mode_changed),
        (window._htoa_row, window._on_htoa_toggled),
        (window._data_tracks_row, window._on_data_tracks_toggled),
        (window._copy_image_row, window._on_copy_image_toggled),
        (window._ar_row, window._on_ar_toggled),
        (window._require_db_match_row, window._on_require_db_match_toggled),
        (window._archival_strict_row, window._on_archival_strict_toggled),
        (window._ctdb_row, window._on_ctdb_toggled),
        (window._submit_ctdb_row, window._on_submit_ctdb_toggled),
        (window._burst_row, window._on_burst_toggled),
        (window._log_row, window._on_log_toggled),
        (window._cue_row, window._on_cue_toggled),
        (window._auto_rip_row, window._on_auto_rip_toggled),
        (window._auto_eject_row, window._on_auto_eject_toggled),
        (window._mb_row, window._on_mb_toggled),
        (window._freedb_row, window._on_freedb_toggled),
        (window._auto_lookup_row, window._on_auto_lookup_toggled),
        (window._rg_row, window._on_rg_toggled),
        (window._sound_check_row, window._on_sound_check_toggled),
        (window._ripper_tag_row, window._on_ripper_tag_toggled),
        (window._fetch_lyrics_row, window._on_fetch_lyrics_toggled),
        (window._embed_lyrics_row, window._on_embed_lyrics_toggled),
        (window._lyrics_mode_row, window._on_lyrics_mode_changed),
        (window._embed_lossy_row, window._on_embed_lossy_toggled),
        (window._embed_lossless_row, window._on_embed_lossless_toggled),
        (window._fetch_art_row, window._on_fetch_art_toggled),
        (window._art_itunes_row, window._on_art_itunes_toggled),
        (window._art_caa_row, window._on_art_caa_toggled),
        (window._art_size_lossy_row, window._on_art_size_lossy_changed),
        (window._art_size_lossless_row, window._on_art_size_lossless_changed),
    ]
    with block_handlers(handlers):
        window._output_row.set_text(settings.output_directory or default_out)
        window._select_combo(
            window._existing_files_row,
            window._existing_files,
            normalize_existing_files_policy(settings.existing_files),
        )
        window._device_row.set_text(settings.device or '/dev/sr0')
        window._load_naming_scheme_state(
            FOLDER_SCHEMES,
            settings.album_folder_template,
            combo=window._folder_scheme_row,
            entry=window._folder_template_row,
            custom_attr='_custom_album_folder_template',
            default_template=DEFAULT_ALBUM_FOLDER_TEMPLATE,
        )
        window._load_naming_scheme_state(
            FILENAME_SCHEMES,
            settings.filename_template,
            combo=window._filename_scheme_row,
            entry=window._filename_row,
            custom_attr='_custom_filename_template',
            default_template=DEFAULT_FILENAME_TEMPLATE,
        )
        window._select_combo(
            window._artist_style_row,
            [sid for sid, _label in ARTIST_STYLES],
            normalize_artist_style(settings.artist_style),
        )
        window._m4a_feat_row.set_active(settings.m4a_feat_artists)
        window._sync_naming_scheme_ui()
        window._select_combo(window._encoder_row, ids, settings.encode_format)
        # Force quality combo to reload from store values.
        window._quality_kind = None
        window._also_quality_kind = None
        window._set_feature_expander(window._also_encode_row, bool(settings.also_encode))
        window._select_combo(
            window._also_format_row,
            window._also_formats,
            normalize_also_encode_format(settings.also_encode_format),
        )
        window._select_combo(
            window._also_folder_row,
            window._also_folders,
            normalize_also_encode_folder(settings.also_encode_folder),
        )
        also_dir = (settings.also_encode_directory or '').strip()
        window._custom_also_directory = also_dir
        # Keep remembered custom path even when Folder ≠ Custom (row is dimmed).
        window._also_directory_row.set_text(also_dir)
        window._sync_encoding_ui()
        window._offset_row.set_value(settings.drive_sample_offset)
        window._update_calibration_row()
        from ready2rip.settings_defs import (
            normalize_rip_mode,
            rip_mode_subtitle,
            rip_mode_tooltip,
        )

        rip_mode = normalize_rip_mode(settings.rip_mode)
        window._select_combo(
            window._rip_mode_row,
            window._rip_modes,
            rip_mode,
        )
        window._rip_mode_row.set_subtitle(rip_mode_subtitle(rip_mode))
        window._rip_mode_row.set_tooltip_text(rip_mode_tooltip(rip_mode))
        window._htoa_row.set_active(settings.rip_htoa)
        window._data_tracks_row.set_active(settings.rip_data_tracks)
        window._copy_image_row.set_active(settings.copy_image)
        window._ar_row.set_active(settings.verify_accuraterip)
        window._require_db_match_row.set_active(settings.require_db_match)
        window._sync_require_db_match_sensitivity(settings.verify_accuraterip)
        window._archival_strict_row.set_active(settings.archival_strict)
        window._ctdb_row.set_active(settings.verify_ctdb)
        window._submit_ctdb_row.set_active(settings.submit_ctdb)
        window._burst_row.set_active(settings.burst_fallback)
        window._sync_rip_mode_sensitivity(rip_mode)
        window._log_row.set_active(settings.write_rip_log)
        window._cue_row.set_active(settings.write_cue_file)
        window._auto_rip_row.set_active(settings.auto_rip)
        window._auto_eject_row.set_active(settings.auto_eject)
        window._set_feature_expander(window._auto_lookup_row, settings.auto_lookup_metadata)
        window._mb_row.set_active(settings.use_musicbrainz)
        window._freedb_row.set_active(settings.use_freedb)
        window._sync_lookup_source_sensitivity(settings.auto_lookup_metadata)
        window._rg_row.set_active(settings.apply_replaygain)
        window._sound_check_row.set_active(settings.write_sound_check)
        window._ripper_tag_row.set_active(settings.add_ripper_tag)
        window._fetch_lyrics_row.set_active(settings.fetch_lyrics)
        window._set_feature_expander(window._embed_lyrics_row, settings.embed_lyrics)
        window._select_combo(
            window._lyrics_mode_row,
            window._lyrics_save_modes,
            normalize_lyrics_save_mode(settings.lyrics_save_mode),
        )
        window._set_feature_expander(window._embed_lossy_row, settings.embed_artwork)
        window._set_feature_expander(window._embed_lossless_row, settings.embed_artwork_lossless)
        window._set_feature_expander(window._fetch_art_row, settings.fetch_artwork)
        window._art_caa_row.set_active(settings.artwork_source_caa)
        window._art_itunes_row.set_active(settings.artwork_source_itunes)
        window._sync_artwork_source_sensitivity(settings.fetch_artwork)
        window._select_art_size_row(
            window._art_size_lossy_row,
            settings.artwork_max_size,
            values=window._art_size_values_lossy,
        )
        window._select_art_size_row(
            window._art_size_lossless_row,
            settings.artwork_max_size_lossless,
            values=window._art_size_values_lossless,
        )

    window._device = settings.device
    window._rebuild_drive_rows(
        settings.device or window._device or '/dev/sr0',
        allow_optical=False,
    )
    if window._artwork is not None:
        window._prepare_embed_artwork()

