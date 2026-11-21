# SPDX-License-Identifier: GPL-3.0-or-later
"""Files / naming preference rows for the main window."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import gi

gi.require_version('Adw', '1')
gi.require_version('Gtk', '4.0')
from gi.repository import Adw, Gtk

from ready2rip.paths import (
    DEFAULT_ALBUM_FOLDER_TEMPLATE,
    DEFAULT_FILENAME_TEMPLATE,
    FILENAME_SCHEMES,
    FILENAME_TEMPLATE_HELP,
    FOLDER_SCHEMES,
    FOLDER_TEMPLATE_HELP,
)
from ready2rip.settings import (
    EXISTING_FILE_POLICIES,
    normalize_existing_files_policy,
)
from ready2rip.tags.artists import ARTIST_STYLES, normalize_artist_style

if TYPE_CHECKING:
    from ready2rip.window import Ready2RipWindow


def build_files_naming_settings(
    window: Ready2RipWindow, settings: Any, default_out: str
) -> None:
    """Populate files + naming groups: output, existing files, templates."""
    window._output_row = Adw.EntryRow(title='Output folder')
    window._output_row.set_text(settings.output_directory or default_out)
    window._output_row.set_tooltip_text(default_out)
    window._output_row.connect('changed', window._on_output_changed)
    window._output_row.add_suffix(
        window._folder_browse_button(window._on_choose_output_folder)
    )
    window._add_pref(window.files_group, window._output_row)

    window._existing_files = [sid for sid, _label in EXISTING_FILE_POLICIES]
    window._existing_files_row = Adw.ComboRow(title='Existing files')
    window._existing_files_row.set_model(
        Gtk.StringList.new([label for _sid, label in EXISTING_FILE_POLICIES])
    )
    window._existing_files_row.set_tooltip_text(
        'Overwrite — replace existing files (default). '
        'Ask — confirm when destinations already exist. '
        'Skip existing — leave present files unchanged. '
        'Keep both — write new files as Title (2).ext'
    )
    window._select_combo(
        window._existing_files_row,
        window._existing_files,
        normalize_existing_files_policy(settings.existing_files),
    )
    window._existing_files_row.connect(
        'notify::selected', window._on_existing_files_changed
    )
    window._add_pref(window.files_group, window._existing_files_row)

    window._folder_scheme_row = Adw.ComboRow(title='Album folder scheme')
    window._folder_template_row = Adw.EntryRow(title='Album folder template')
    window._folder_template_row.set_tooltip_text(FOLDER_TEMPLATE_HELP)
    window._filename_scheme_row = Adw.ComboRow(title='Track filename scheme')
    window._filename_row = Adw.EntryRow(title='Track filename template')
    window._filename_row.set_tooltip_text(FILENAME_TEMPLATE_HELP)

    window._bind_naming_scheme_combo(
        window._folder_scheme_row,
        FOLDER_SCHEMES,
        settings.album_folder_template,
        entry=window._folder_template_row,
        custom_attr='_custom_album_folder_template',
        default_template=DEFAULT_ALBUM_FOLDER_TEMPLATE,
    )
    window._bind_naming_scheme_combo(
        window._filename_scheme_row,
        FILENAME_SCHEMES,
        settings.filename_template,
        entry=window._filename_row,
        custom_attr='_custom_filename_template',
        default_template=DEFAULT_FILENAME_TEMPLATE,
    )

    window._folder_scheme_row.connect(
        'notify::selected', window._on_folder_scheme_changed
    )
    window._folder_template_row.connect('changed', window._on_folder_template_changed)
    window._filename_scheme_row.connect(
        'notify::selected', window._on_filename_scheme_changed
    )
    window._filename_row.connect('changed', window._on_filename_changed)

    window._add_pref(window.naming_group, window._folder_scheme_row)
    window._add_pref(window.naming_group, window._folder_template_row)
    window._add_pref(window.naming_group, window._filename_scheme_row)
    window._add_pref(window.naming_group, window._filename_row)
    window._sync_naming_scheme_ui()

    window._artist_style_row = Adw.ComboRow(title='Multiple artists')
    window._artist_style_row.set_model(
        Gtk.StringList.new([label for _sid, label in ARTIST_STYLES])
    )
    window._select_combo(
        window._artist_style_row,
        [sid for sid, _label in ARTIST_STYLES],
        normalize_artist_style(settings.artist_style),
    )
    window._artist_style_row.set_tooltip_text(
        'How multiple artists appear in filenames and tags. '
        'semicolon / slash keep native multi-value artist fields. '
        'feat. writes a single artist string '
        '(Primary feat. Guest & Other) and leaves the title clean'
    )
    window._artist_style_row.connect(
        'notify::selected', window._on_artist_style_changed
    )
    window._add_pref(window.naming_group, window._artist_style_row)

    window._m4a_feat_row = window._make_switch_row(
        title='feat. artists for M4A',
        tooltip=(
            'Write a single “Primary feat. Guests” artist string on '
            'M4A/AAC/ALAC/QAAC for iPod and Apple Music compatibility, '
            'even when Multiple artists is semicolon or slash. '
            'FLAC and other formats keep the Multiple artists style'
        ),
        active=settings.m4a_feat_artists,
        handler=window._on_m4a_feat_toggled,
    )
    window._add_pref(window.naming_group, window._m4a_feat_row)
