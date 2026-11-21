# SPDX-License-Identifier: GPL-3.0-or-later
"""Encoding preference rows for the main window."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import gi

gi.require_version('Adw', '1')
gi.require_version('Gtk', '4.0')
from gi.repository import Adw, Gtk

from ready2rip.settings import (
    ALSO_ENCODERS,
    ALSO_FOLDER_LAYOUTS,
    ENCODERS,
    normalize_also_encode_folder,
    normalize_also_encode_format,
)

if TYPE_CHECKING:
    from ready2rip.window import Ready2RipWindow


def build_encoding_settings(
    window: Ready2RipWindow, settings: Any, ids: list[str]
) -> None:
    """Populate encoding group: primary format + also-encode expander."""
    window._encoder_row = Adw.ComboRow(title='Format')
    window._encoder_row.set_tooltip_text(
        'Primary format is always lossless (FLAC, WAV, AIFF, WavPack, or ALAC)'
    )
    window._encoder_row.set_model(Gtk.StringList.new([label for _id, label in ENCODERS]))
    window._select_combo(window._encoder_row, ids, settings.encode_format)
    window._encoder_row.connect('notify::selected', window._on_encoder_changed)
    window._add_pref(window.encoding_group, window._encoder_row)

    window._quality_row = Adw.ComboRow(title='Quality')
    window._quality_row.connect('notify::selected', window._on_quality_changed)
    window._add_pref(window.encoding_group, window._quality_row)

    window._also_encode_row = window._make_feature_expander(
        title='Also encode lossy',
        tooltip='Encode a second lossy copy from the same verified rip',
        enabled=bool(settings.also_encode),
        handler=window._on_also_encode_toggled,
    )

    window._also_format_row = Adw.ComboRow(title='Format')
    window._also_format_row.set_tooltip_text(
        'Lossy companion: LAME MP3, FDK AAC, QAAC, or Opus'
    )
    window._also_format_row.set_model(
        Gtk.StringList.new([label for _id, label in ALSO_ENCODERS])
    )
    window._select_combo(
        window._also_format_row,
        window._also_formats,
        normalize_also_encode_format(settings.also_encode_format),
    )
    window._also_format_row.connect('notify::selected', window._on_also_format_changed)
    window._also_encode_row.add_row(window._also_format_row)

    window._also_quality_row = Adw.ComboRow(title='Quality')
    window._also_quality_row.connect(
        'notify::selected', window._on_also_quality_changed
    )
    window._also_encode_row.add_row(window._also_quality_row)

    window._also_folder_row = Adw.ComboRow(title='Folder')
    window._also_folder_row.set_model(
        Gtk.StringList.new([label for _id, label in ALSO_FOLDER_LAYOUTS])
    )
    window._also_folder_row.set_tooltip_text(
        'Subfolder by format — Album/FLAC and Album/MP3. '
        'Same album folder — both files together. '
        'Custom folder — choose a second output root'
    )
    window._select_combo(
        window._also_folder_row,
        window._also_folders,
        normalize_also_encode_folder(settings.also_encode_folder),
    )
    window._also_folder_row.connect('notify::selected', window._on_also_folder_changed)
    window._also_encode_row.add_row(window._also_folder_row)

    also_dir_default = (settings.also_encode_directory or '').strip()
    window._custom_also_directory = also_dir_default
    window._also_directory_row = Adw.EntryRow(title='Custom folder')
    if also_dir_default:
        window._also_directory_row.set_text(also_dir_default)
    window._also_directory_row.set_tooltip_text(
        'Output root for the lossy encode when Folder is Custom folder'
    )
    window._also_directory_row.connect('changed', window._on_also_directory_changed)
    window._also_directory_row.add_suffix(
        window._folder_browse_button(window._on_choose_also_directory)
    )
    window._also_encode_row.add_row(window._also_directory_row)

    window._add_pref(window.encoding_group, window._also_encode_row)
    window._sync_encoding_ui()
