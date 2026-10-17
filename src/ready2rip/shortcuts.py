# SPDX-License-Identifier: GPL-3.0-or-later
"""Keyboard shortcuts overlay (GNOME HIG)."""

from __future__ import annotations

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gtk

_GROUPS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    (
        'General',
        (
            ('Quit', '<primary>q'),
            ('Close window', '<primary>w'),
            ('Settings', '<primary>comma'),
            ('Keyboard shortcuts', '<primary>question'),
            ('Main menu', 'F10'),
        ),
    ),
    (
        'Disc',
        (
            ('Rip', '<primary>r'),
            ('Eject', '<primary>e'),
            ('Play / Pause', '<primary>p'),
            ('Next track', '<alt>Right'),
            ('Previous track', '<alt>Left'),
        ),
    ),
)


def present_keyboard_shortcuts(parent: Gtk.Window | None) -> None:
    """Show a compact shortcuts dialog (no search bar)."""
    # Prefer a Preferences-style dialog: Adw.ShortcutsDialog always includes a
    # search entry, which is noise for this small shortcut set.
    dialog = Adw.Dialog(title='Keyboard Shortcuts')
    dialog.set_content_width(420)
    dialog.set_follows_content_size(True)

    toolbar = Adw.ToolbarView()
    toolbar.add_top_bar(Adw.HeaderBar())

    page = Adw.PreferencesPage()
    for title, rows in _GROUPS:
        group = Adw.PreferencesGroup(title=title)
        for name, accel in rows:
            row = Adw.ActionRow(title=name, activatable=False)
            label_cls = getattr(Gtk, 'ShortcutLabel', None) or getattr(
                Gtk, 'ShortcutsLabel', None
            )
            if label_cls is not None:
                badge = label_cls(accelerator=accel)
                badge.set_valign(Gtk.Align.CENTER)
                row.add_suffix(badge)
            else:
                row.set_subtitle(accel)
            group.add(row)
        page.add(group)

    toolbar.set_content(page)
    dialog.set_child(toolbar)
    dialog.present(parent)
