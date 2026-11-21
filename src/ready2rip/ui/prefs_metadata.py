# SPDX-License-Identifier: GPL-3.0-or-later
"""Metadata preference rows for the main window."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import gi

gi.require_version('Adw', '1')
gi.require_version('Gtk', '4.0')
from gi.repository import Adw, Gtk

from ready2rip.settings import (
    ARTWORK_SIZES,
    ARTWORK_SIZES_LOSSLESS,
    LYRICS_SAVE_MODES,
    normalize_lyrics_save_mode,
)

if TYPE_CHECKING:
    from ready2rip.window import Ready2RipWindow


def build_embed_size_expander(
    window: Ready2RipWindow,
    *,
    title: str,
    tooltip: str,
    enabled: bool,
    size: int,
    size_tooltip: str,
    sizes: tuple[tuple[int, str], ...],
    on_toggle,
    on_size,
) -> tuple[Adw.ExpanderRow, Adw.ComboRow]:
    """Feature expander with an embed-size ComboRow."""
    expander = window._make_feature_expander(
        title=title,
        tooltip=tooltip,
        enabled=enabled,
        handler=on_toggle,
    )
    size_row = Adw.ComboRow(title='Size')
    size_row.set_model(Gtk.StringList.new([label for _px, label in sizes]))
    window._select_art_size_row(size_row, size, values=[px for px, _ in sizes])
    size_row.set_tooltip_text(size_tooltip)
    size_row.connect('notify::selected', on_size)
    expander.add_row(size_row)
    return expander, size_row


def build_metadata_rows(window: Ready2RipWindow, settings: Any) -> None:
    """Populate Metadata page: Lookup, Artwork, Tags."""
    window._auto_lookup_row = window._make_feature_expander(
        title='Look up automatically',
        tooltip='Fetch album and track metadata when a disc is detected',
        enabled=settings.auto_lookup_metadata,
        handler=window._on_auto_lookup_toggled,
    )
    window._mb_row = window._make_switch_row(
        title='MusicBrainz',
        tooltip='Preferred source for album and track metadata',
        active=settings.use_musicbrainz,
        handler=window._on_mb_toggled,
    )
    window._freedb_row = window._make_switch_row(
        title='gnudb',
        tooltip='FreeDB-compatible disc lookup (fallback)',
        active=settings.use_freedb,
        handler=window._on_freedb_toggled,
    )
    window._auto_lookup_row.add_row(window._mb_row)
    window._auto_lookup_row.add_row(window._freedb_row)
    window._add_pref(window.lookup_group, window._auto_lookup_row)
    window._sync_lookup_source_sensitivity(settings.auto_lookup_metadata)

    window._fetch_art_row = window._make_feature_expander(
        title='Download artwork',
        tooltip='Download the best available cover art for the release',
        enabled=settings.fetch_artwork,
        handler=window._on_fetch_art_toggled,
    )
    window._art_caa_row = window._make_switch_row(
        title='Cover Art Archive',
        tooltip='Artwork from the MusicBrainz Cover Art Archive',
        active=settings.artwork_source_caa,
        handler=window._on_art_caa_toggled,
    )
    window._art_itunes_row = window._make_switch_row(
        title='iTunes',
        tooltip='Artwork from the Apple Music / iTunes store',
        active=settings.artwork_source_itunes,
        handler=window._on_art_itunes_toggled,
    )
    window._fetch_art_row.add_row(window._art_caa_row)
    window._fetch_art_row.add_row(window._art_itunes_row)
    window._add_pref(window.artwork_group, window._fetch_art_row)
    window._sync_artwork_source_sensitivity(settings.fetch_artwork)

    window._art_size_values_lossy = [px for px, _label in ARTWORK_SIZES]
    window._art_size_values_lossless = [px for px, _label in ARTWORK_SIZES_LOSSLESS]
    window._embed_lossy_row, window._art_size_lossy_row = build_embed_size_expander(
        window,
        title='Embed in lossy',
        tooltip=(
            'Embed cover art into MP3 / M4A / Opus '
            '(no loose cover.jpg beside lossy files)'
        ),
        enabled=settings.embed_artwork,
        size=settings.artwork_max_size,
        size_tooltip='Longest edge for lossy embeds (300–800 px). Default 600.',
        sizes=ARTWORK_SIZES,
        on_toggle=window._on_embed_lossy_toggled,
        on_size=window._on_art_size_lossy_changed,
    )
    window._embed_lossless_row, window._art_size_lossless_row = build_embed_size_expander(
        window,
        title='Embed in lossless',
        tooltip=(
            'Embed cover art into FLAC / WAV / AIFF / WavPack / ALAC. '
            'Folder cover.jpg stays full resolution'
        ),
        enabled=settings.embed_artwork_lossless,
        size=settings.artwork_max_size_lossless,
        size_tooltip='Longest edge for lossless embeds (300–1200 px). Default 600.',
        sizes=ARTWORK_SIZES_LOSSLESS,
        on_toggle=window._on_embed_lossless_toggled,
        on_size=window._on_art_size_lossless_changed,
    )
    window._add_pref(window.artwork_group, window._embed_lossy_row)
    window._add_pref(window.artwork_group, window._embed_lossless_row)

    for attr, title, tooltip, active, handler in (
        (
            '_rg_row',
            'ReplayGain',
            'Write track and album ReplayGain loudness tags',
            settings.apply_replaygain,
            window._on_rg_toggled,
        ),
        (
            '_sound_check_row',
            'iTunes Sound Check',
            'Write iTunNORM for Sound Check on MP3, M4A, AIFF, and WAV '
            '(skipped for FLAC, WavPack, and Opus)',
            settings.write_sound_check,
            window._on_sound_check_toggled,
        ),
        (
            '_ripper_tag_row',
            'Ripper tag',
            'Write ready2rip and its version into Encoded by. '
            'Encoder always records the codec tool and version',
            settings.add_ripper_tag,
            window._on_ripper_tag_toggled,
        ),
        (
            '_fetch_lyrics_row',
            'Fetch lyrics',
            'Look up lyrics on LRCLIB after metadata '
            '(artist, title, album, and duration)',
            settings.fetch_lyrics,
            window._on_fetch_lyrics_toggled,
        ),
    ):
        row = window._make_switch_row(
            title=title, tooltip=tooltip, active=active, handler=handler
        )
        setattr(window, attr, row)
        window._add_pref(window.tags_group, row)

    window._lyrics_save_modes = [mid for mid, _label in LYRICS_SAVE_MODES]
    window._embed_lyrics_row = window._make_feature_expander(
        title='Save lyrics (lossless)',
        tooltip=(
            'Write lyrics with lossless rips only. '
            'Lossy Also encode companions are skipped'
        ),
        enabled=settings.embed_lyrics,
        handler=window._on_embed_lyrics_toggled,
    )
    window._lyrics_mode_row = Adw.ComboRow(title='Save as')
    window._lyrics_mode_row.set_model(
        Gtk.StringList.new([label for _mid, label in LYRICS_SAVE_MODES])
    )
    window._select_combo(
        window._lyrics_mode_row,
        window._lyrics_save_modes,
        normalize_lyrics_save_mode(settings.lyrics_save_mode),
    )
    window._lyrics_mode_row.set_tooltip_text(
        'Embed in tags, write .lrc/.txt sidecars beside each track, or both'
    )
    window._lyrics_mode_row.connect('notify::selected', window._on_lyrics_mode_changed)
    window._embed_lyrics_row.add_row(window._lyrics_mode_row)
    window._add_pref(window.tags_group, window._embed_lyrics_row)
