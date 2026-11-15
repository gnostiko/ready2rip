# SPDX-License-Identifier: GPL-3.0-or-later
"""Dialog to pick among multiple metadata candidates."""

from __future__ import annotations

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, GLib, Gtk

from ready2rip.metadata.providers import AlbumMetadata


class MetadataPickerDialog(Adw.Dialog):
    """Let the user choose one album match from MusicBrainz / gnudb."""

    def __init__(
        self,
        candidates: list[AlbumMetadata],
        *,
        disc_track_count: int = 0,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.set_title('Choose release')
        self.set_content_width(560)
        self.set_content_height(520)
        self._candidates = list(candidates)
        self._disc_track_count = max(0, int(disc_track_count or 0))
        self._chosen: AlbumMetadata | None = None
        self._rows: list[tuple[Gtk.CheckButton, AlbumMetadata]] = []

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        cancel = Gtk.Button(label='Cancel')
        cancel.add_css_class('flat')
        cancel.connect('clicked', lambda *_: self.close())
        header.pack_start(cancel)

        apply_btn = Gtk.Button(label='Use selected')
        apply_btn.add_css_class('suggested-action')
        apply_btn.add_css_class('pill')
        apply_btn.connect('clicked', self._on_apply)
        header.pack_end(apply_btn)
        toolbar.add_top_bar(header)

        scroll = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
        clamp = Adw.Clamp(maximum_size=520, tightening_threshold=400)
        if self._disc_track_count > 0:
            description = (
                f'Choose the release that matches this disc '
                f'({self._disc_track_count} tracks)'
            )
        else:
            description = 'Choose the release that matches this disc'
        group = Adw.PreferencesGroup(
            title='Matches',
            description=description,
            margin_top=12,
            margin_bottom=12,
            margin_start=12,
            margin_end=12,
        )

        group_root: Gtk.CheckButton | None = None
        for i, album in enumerate(self._candidates):
            # ActionRow title/subtitle are Pango markup (& in labels breaks otherwise).
            row = Adw.ActionRow(
                title=GLib.markup_escape_text(album.title or 'Unknown Album'),
                subtitle=GLib.markup_escape_text(
                    self._subtitle(album, disc_track_count=self._disc_track_count)
                ),
                activatable=True,
            )
            check = Gtk.CheckButton()
            if i == 0:
                check.set_active(True)
                group_root = check
            elif group_root is not None:
                check.set_group(group_root)
            row.add_prefix(check)
            row.set_activatable_widget(check)

            suffix = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            suffix.set_valign(Gtk.Align.CENTER)

            mismatch = self._track_count_mismatch(album, self._disc_track_count)
            if mismatch is not None:
                warn = Gtk.Label(label=mismatch)
                warn.add_css_class('caption')
                warn.add_css_class('error')
                warn.set_tooltip_text(
                    'This medium’s track count does not match the inserted disc'
                )
                suffix.append(warn)

            source = (album.source or 'unknown').strip() or 'unknown'
            badge = Gtk.Label(label=source)
            badge.add_css_class('dim-label')
            badge.add_css_class('caption')
            suffix.append(badge)
            row.add_suffix(suffix)

            group.add(row)
            self._rows.append((check, album))

        clamp.set_child(group)
        scroll.set_child(clamp)
        toolbar.set_content(scroll)
        self.set_child(toolbar)

    @property
    def chosen(self) -> AlbumMetadata | None:
        """Release selected when the dialog closes, or ``None`` if cancelled."""
        return self._chosen

    @staticmethod
    def _disc_label(album: AlbumMetadata) -> str | None:
        """Return ``Disc N/M`` when the release is multi-disc or not disc 1."""
        try:
            position = max(1, int(album.medium_position or 1))
        except (TypeError, ValueError):
            position = 1
        try:
            count = max(1, int(album.medium_count or 1))
        except (TypeError, ValueError):
            count = 1
        if count > 1 or position > 1:
            return f'Disc {position}/{count}'
        return None

    @staticmethod
    def _track_count_mismatch(album: AlbumMetadata, disc_track_count: int) -> str | None:
        if disc_track_count <= 0 or not album.tracks:
            return None
        n = len(album.tracks)
        if n == disc_track_count:
            return None
        return f'{n} ≠ {disc_track_count}'

    @staticmethod
    def _subtitle(album: AlbumMetadata, *, disc_track_count: int = 0) -> str:
        bits = [album.artist or 'Unknown Artist']
        if album.date:
            bits.append(album.date[:4] if len(album.date) >= 4 else album.date)
        disc = MetadataPickerDialog._disc_label(album)
        if disc:
            bits.append(disc)
        if album.tracks:
            n = len(album.tracks)
            if disc_track_count > 0 and n != disc_track_count:
                bits.append(f'{n} tracks (disc has {disc_track_count})')
            else:
                bits.append(f'{n} tracks')
        if album.status and album.status.lower() not in {'', 'official'}:
            bits.append(album.status)
        if album.country:
            bits.append(album.country)
        if album.disambiguation:
            bits.append(album.disambiguation)
        if album.label:
            bits.append(album.label)
        if album.catalog_number:
            bits.append(album.catalog_number)
        if album.genre:
            bits.append(album.genre)
        return ' · '.join(bits)

    def _on_apply(self, *_args) -> None:
        for check, album in self._rows:
            if check.get_active():
                self._chosen = album
                break
        self.close()
