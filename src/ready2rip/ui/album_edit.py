# SPDX-License-Identifier: GPL-3.0-or-later
"""Album and track metadata edit rows for the main window."""

from __future__ import annotations

from typing import TYPE_CHECKING

import gi

gi.require_version('Adw', '1')
gi.require_version('Gtk', '4.0')
from gi.repository import Adw, Gtk, Pango

from ready2rip.disc.probe import DiscInfo
from ready2rip.paths import track_meta_for
from ready2rip.tags.artists import normalize_artists

if TYPE_CHECKING:
    from ready2rip.window import Ready2RipWindow


def build_album_edit_rows(window: Ready2RipWindow) -> None:
    """Always-visible Album EntryRows (no expander)."""
    for row in window._album_field_rows:
        window.album_edit_group.remove(row)
    window._album_field_rows.clear()

    title_row = Adw.EntryRow(title='Title')
    title_row.connect('changed', window._on_album_field_changed)
    window.album_edit_group.add(title_row)
    window._album_field_rows.append(title_row)
    window._album_title_row = title_row

    artist_row = Adw.EntryRow(title='Album artist')
    artist_row.set_tooltip_text(
        'Separate multiple artists with a semicolon '
        '(for example Artist One; Artist Two)'
    )
    artist_row.connect('changed', window._on_album_field_changed)
    window.album_edit_group.add(artist_row)
    window._album_field_rows.append(artist_row)
    window._album_artist_row = artist_row

    date_row = Adw.EntryRow(title='Date')
    date_row.set_tooltip_text(
        'Release date as YYYY, YYYY-MM, or YYYY-MM-DD '
        '(for example 1997-03-01)'
    )
    date_row.connect('changed', window._on_album_field_changed)
    window.album_edit_group.add(date_row)
    window._album_field_rows.append(date_row)
    window._album_date_row = date_row

    genre_row = Adw.EntryRow(title='Genre')
    genre_row.set_tooltip_text(
        'Album genre (for example Rock or Jazz). Prefers MusicBrainz; '
        'falls back to gnudb or CD-Text'
    )
    genre_row.connect('changed', window._on_album_field_changed)
    window.album_edit_group.add(genre_row)
    window._album_field_rows.append(genre_row)
    window._album_genre_row = genre_row

    disc_row = Adw.EntryRow(title='Disc')
    disc_row.set_text('1/1')
    disc_row.set_tooltip_text(
        'Disc number as N/M (for example 1/1 or 2/3)'
    )
    disc_row.connect('changed', window._on_album_field_changed)
    window.album_edit_group.add(disc_row)
    window._album_field_rows.append(disc_row)
    window._album_disc_row = disc_row
    window._set_album_fields_editable(not window._ripping)
    listbox = window._preferences_listbox(window.album_edit_group)
    if listbox is not None:
        listbox.connect('notify::height', window._on_art_album_geometry)
    # Building rows can steal focus into Title; drop it until the user clicks.
    window._defer_clear_window_focus()
    window._on_art_album_geometry()


def rebuild_track_list(window: Ready2RipWindow, info: DiscInfo | None) -> None:
    group = window.track_edit_group
    for row in window._track_rows:
        group.remove(row)
    window._track_rows.clear()
    window._track_checks.clear()
    window._track_title_entries.clear()
    window._track_artist_entries.clear()
    window._track_title_labels.clear()
    window._track_artist_labels.clear()
    window._track_status_labels.clear()
    window._track_title_fields = {}
    window._track_artist_fields = {}
    window._track_edit_button = None
    window._track_artist_button = None
    window._track_select_all = None
    if window._track_header_suffix is not None:
        group.set_header_suffix(None)
        window._track_header_suffix = None
    # New disc/list always starts in view mode.
    window._tracks_editing = False
    # Fresh size groups so rebuilt widgets align cleanly.
    window._track_lead_size = Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL)
    window._track_status_size = Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL)
    window._track_dur_size = Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL)

    if info is None:
        group.set_title('Tracks')
        return

    # Edit flow: Edit/Done always; “Album artist” only while editing.
    header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    btn_edit = Gtk.Button(label='Edit')
    btn_edit.add_css_class('flat')
    btn_edit.set_tooltip_text('Edit track titles and artists')
    btn_edit.connect('clicked', window._on_tracks_edit_toggled)
    btn_artist = Gtk.Button(label='Album artist')
    btn_artist.add_css_class('flat')
    btn_artist.set_tooltip_text('Copy the album artist into tracks that have no artist')
    btn_artist.set_visible(False)
    btn_artist.connect('clicked', lambda *_: window._fill_empty_track_artists())
    header_box.append(btn_artist)
    header_box.append(btn_edit)
    group.set_header_suffix(header_box)
    window._track_header_suffix = header_box
    window._track_edit_button = btn_edit
    window._track_artist_button = btn_artist

    # Column header: Title | Artist — keeps artist column aligned under “Tracks”.
    col_header = make_track_column_header(window)
    group.add(col_header)
    window._track_rows.append(col_header)

    album = window._ensure_album_tracks()
    album_artist = normalize_artists(album.artist or '')

    window._suppress_meta_write = True
    try:
        for track in info.tracks:
            meta = track_meta_for(album, track.number)
            title = meta.title if meta else ''
            artist = normalize_artists((meta.artist if meta else '') or '')
            if not artist and album_artist and window._meta_cache.has_useful_metadata(album):
                artist = album_artist

            row = make_track_edit_row(
                window,
                track.number,
                title=title,
                artist=artist,
                duration_label=track.duration_label,
            )
            group.add(row)
            window._track_rows.append(row)
    finally:
        window._suppress_meta_write = False

    group.set_title(f'Tracks · {info.track_count}')
    window._apply_tracks_edit_mode()
    # Rebuild while ripping / Copy Image must keep select + Also encode in sync.
    window._sync_copy_image_ui()


def align_track_row_baseline(*widgets: Gtk.Widget) -> None:
    """Keep checkbox, #, title, artist, and time on one text baseline."""
    for widget in widgets:
        widget.set_valign(Gtk.Align.BASELINE)


def make_title_artist_grid(title: Gtk.Widget, artist: Gtk.Widget) -> Gtk.Grid:
    """Homogeneous 2-col grid: Title and Artist each take half."""
    grid = Gtk.Grid()
    grid.set_column_homogeneous(True)
    grid.set_column_spacing(12)
    grid.set_hexpand(True)
    grid.set_halign(Gtk.Align.FILL)
    grid.set_baseline_row(0)
    for child in (title, artist):
        child.set_hexpand(True)
        child.set_halign(Gtk.Align.FILL)
        child.set_valign(Gtk.Align.BASELINE)
    grid.attach(title, 0, 0, 1, 1)
    grid.attach(artist, 1, 0, 1, 1)
    return grid


def track_caption(
    text: str,
    *extra_classes: str,
    xalign: float = 0.0,
) -> Gtk.Label:
    label = Gtk.Label(label=text)
    label.add_css_class('caption')
    label.add_css_class('dim-label')
    for name in extra_classes:
        label.add_css_class(name)
    label.set_xalign(xalign)
    label.set_valign(Gtk.Align.BASELINE)
    return label


def make_track_field(
    window: Ready2RipWindow,
    *,
    number: int,
    value: str,
    empty_display: str,
    placeholder: str,
    tooltip: str,
    css_class: str | None = None,
) -> tuple[Gtk.Box, Gtk.Label, Gtk.Entry]:
    """Title/Artist cell: view label and edit entry share the row baseline.

    A Box with show/hide is used instead of Gtk.Stack so the visible text
    participates in baseline layout with the checkbox and track number.
    """
    field = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
    field.set_hexpand(True)
    field.set_halign(Gtk.Align.FILL)
    field.set_valign(Gtk.Align.BASELINE)
    field.set_baseline_position(Gtk.BaselinePosition.CENTER)
    if css_class:
        field.add_css_class(css_class)

    display = value or empty_display
    label = Gtk.Label(label=display)
    label.add_css_class('ready2rip-track-text')
    label.set_hexpand(True)
    label.set_halign(Gtk.Align.START)
    label.set_xalign(0.0)
    label.set_ellipsize(Pango.EllipsizeMode.END)
    label.set_valign(Gtk.Align.BASELINE)
    if not value:
        label.add_css_class('dim-label')

    entry = Gtk.Entry()
    entry.set_text(value)
    entry.set_placeholder_text(placeholder)
    entry.set_hexpand(True)
    entry.set_valign(Gtk.Align.BASELINE)
    entry.add_css_class('ready2rip-track-entry')
    entry.set_tooltip_text(tooltip)
    entry.set_visible(False)
    entry.connect(
        'changed',
        lambda *_a, n=number: window._on_track_field_changed(n),
    )

    field.append(label)
    field.append(entry)
    return field, label, entry


def make_track_column_header(window: Ready2RipWindow) -> Gtk.ListBoxRow:
    """Column titles so Title / Artist line up with track cells."""
    row = Gtk.ListBoxRow()
    row.set_activatable(False)
    row.set_selectable(False)
    row.add_css_class('ready2rip-track-header')

    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
    box.add_css_class('ready2rip-track-row')
    box.set_hexpand(True)
    box.set_baseline_position(Gtk.BaselinePosition.CENTER)

    lead = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
    lead.add_css_class('ready2rip-track-lead')
    lead.set_baseline_position(Gtk.BaselinePosition.CENTER)
    select_all = Gtk.CheckButton(active=True)
    select_all.set_tooltip_text('Select or deselect all tracks for ripping')
    select_all.connect('toggled', window._on_select_all_toggled)
    window._track_select_all = select_all
    num_ph = track_caption('#', 'ready2rip-track-num', xalign=0.5)
    align_track_row_baseline(select_all, num_ph, lead)
    lead.append(select_all)
    lead.append(num_ph)
    window._track_lead_size.add_widget(lead)
    box.append(lead)

    title_h = track_caption('Title', 'heading')
    title_h.set_halign(Gtk.Align.START)
    artist_h = track_caption('Artist', 'heading', 'ready2rip-track-artist')
    artist_h.set_halign(Gtk.Align.START)
    fields = make_title_artist_grid(title_h, artist_h)
    align_track_row_baseline(fields)
    box.append(fields)

    status_h = track_caption('Verify', 'ready2rip-track-status', xalign=1.0)
    status_h.set_tooltip_text('AccurateRip result after a rip')
    window._track_status_size.add_widget(status_h)
    box.append(status_h)

    dur_h = track_caption('Time', 'ready2rip-track-dur', xalign=1.0)
    window._track_dur_size.add_widget(dur_h)
    box.append(dur_h)

    row.set_child(box)
    return row


def make_track_edit_row(
    window: Ready2RipWindow,
    number: int,
    *,
    title: str,
    artist: str,
    duration_label: str,
) -> Gtk.ListBoxRow:
    """Flat track row: checkbox · # · title · artist · status · time.

    View mode shows labels; Edit mode swaps in entries for title/artist.
    Title/Artist share remaining width equally (1/2 each).
    """
    row = Gtk.ListBoxRow()
    row.set_activatable(False)
    row.set_selectable(False)
    row.set_tooltip_text(f'Track {number:02d} · {duration_label}')

    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
    box.add_css_class('ready2rip-track-row')
    box.set_hexpand(True)
    box.set_baseline_position(Gtk.BaselinePosition.CENTER)

    lead = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
    lead.add_css_class('ready2rip-track-lead')
    lead.set_baseline_position(Gtk.BaselinePosition.CENTER)
    check = Gtk.CheckButton(active=True)
    check.set_tooltip_text(f'Include track {number:02d} in the rip')
    check.connect('toggled', window._on_track_check_toggled)
    num = Gtk.Label(label=f'{number:02d}')
    num.add_css_class('ready2rip-track-text')
    num.add_css_class('ready2rip-track-num')
    num.add_css_class('dim-label')
    num.set_xalign(0.5)
    align_track_row_baseline(check, num, lead)
    lead.append(check)
    lead.append(num)
    window._track_lead_size.add_widget(lead)
    box.append(lead)

    title_field, title_label, title_entry = make_track_field(
        window,
        number=number,
        value=title,
        empty_display=f'Track {number:02d}',
        placeholder=f'Track {number:02d} title',
        tooltip=f'Title for track {number:02d}',
    )
    artist_field, artist_label, artist_entry = make_track_field(
        window,
        number=number,
        value=artist,
        empty_display='—',
        placeholder='Artist One; Artist Two',
        tooltip=(
            f'Artist for track {number:02d}. '
            'Separate multiple artists with a semicolon '
            '(for example A; B)'
        ),
        css_class='ready2rip-track-artist',
    )
    fields = make_title_artist_grid(title_field, artist_field)
    align_track_row_baseline(fields)
    box.append(fields)

    status = Gtk.Label(label='')
    status.add_css_class('ready2rip-track-text')
    status.add_css_class('ready2rip-track-status')
    status.add_css_class('dim-label')
    status.set_ellipsize(Pango.EllipsizeMode.END)
    status.set_width_chars(10)
    status.set_max_width_chars(28)
    status.set_xalign(1.0)
    status.set_valign(Gtk.Align.BASELINE)
    # Always allocated so the Verify column lines up with the header.
    window._track_status_size.add_widget(status)
    box.append(status)

    # Same text metrics as Title/Artist values (not caption-sized headers).
    dur = Gtk.Label(label=duration_label)
    dur.add_css_class('ready2rip-track-text')
    dur.add_css_class('ready2rip-track-dur')
    dur.set_xalign(1.0)
    dur.set_valign(Gtk.Align.BASELINE)
    dur.set_tooltip_text(f'Duration {duration_label}')
    window._track_dur_size.add_widget(dur)
    box.append(dur)

    row.set_child(box)

    window._track_checks[number] = check
    window._track_title_entries[number] = title_entry
    window._track_artist_entries[number] = artist_entry
    window._track_title_labels[number] = title_label
    window._track_artist_labels[number] = artist_label
    window._track_status_labels[number] = status
    window._track_title_fields[number] = title_field
    window._track_artist_fields[number] = artist_field
    return row