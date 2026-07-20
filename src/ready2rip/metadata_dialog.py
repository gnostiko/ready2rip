# SPDX-License-Identifier: GPL-3.0-or-later
"""Dialog to pick among multiple metadata candidates."""

from __future__ import annotations

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gtk  # noqa: E402

from ready2rip.metadata.providers import AlbumMetadata  # noqa: E402


class MetadataPickerDialog(Adw.Dialog):
    """Let the user choose one album match from MusicBrainz / FreeDB."""

    def __init__(self, candidates: list[AlbumMetadata], **kwargs) -> None:
        super().__init__(**kwargs)
        self.set_title('Choose release')
        self.set_content_width(520)
        self.set_content_height(480)
        self._candidates = list(candidates)
        self._chosen: AlbumMetadata | None = None
        self._rows: list[tuple[Gtk.CheckButton, AlbumMetadata]] = []

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        cancel = Gtk.Button(label='Cancel')
        cancel.connect('clicked', lambda *_: self.close())
        header.pack_start(cancel)

        apply_btn = Gtk.Button(label='Use selected')
        apply_btn.add_css_class('suggested-action')
        apply_btn.connect('clicked', self._on_apply)
        header.pack_end(apply_btn)
        toolbar.add_top_bar(header)

        scroll = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
        clamp = Adw.Clamp(maximum_size=500, tightening_threshold=400)
        group = Adw.PreferencesGroup(
            title='Matches',
            description='Select the release that matches your disc',
            margin_top=12,
            margin_bottom=12,
            margin_start=12,
            margin_end=12,
        )

        group_root: Gtk.CheckButton | None = None
        for i, album in enumerate(self._candidates):
            row = Adw.ActionRow(
                title=album.title or 'Unknown Album',
                subtitle=self._subtitle(album),
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

            source = album.source or 'unknown'
            badge = Gtk.Label(label=source)
            badge.add_css_class('dim-label')
            badge.add_css_class('caption')
            row.add_suffix(badge)

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
    def _subtitle(album: AlbumMetadata) -> str:
        bits = [album.artist or 'Unknown Artist']
        if album.date:
            bits.append(album.date[:4] if len(album.date) >= 4 else album.date)
        if album.country:
            bits.append(album.country)
        if album.tracks:
            bits.append(f'{len(album.tracks)} tracks')
        if album.disambiguation:
            bits.append(album.disambiguation)
        if album.label:
            bits.append(album.label)
        return ' · '.join(bits)

    def _on_apply(self, *_args) -> None:
        for check, album in self._rows:
            if check.get_active():
                self._chosen = album
                break
        self.close()
