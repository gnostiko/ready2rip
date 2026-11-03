# SPDX-License-Identifier: GPL-3.0-or-later
"""Adwaita Application subclass."""

from __future__ import annotations

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gio, GLib, Gtk

from ready2rip import config
from ready2rip.drive_setup import needs_drive_setup
from ready2rip.icons import register_application_icons
from ready2rip.settings import SettingsStore
from ready2rip.shortcuts import present_keyboard_shortcuts
from ready2rip.theme import apply_color_scheme


class Application(Adw.Application):
    """Main application object.

    Use ``startup`` / ``activate`` signal handlers instead of ``do_startup`` /
    ``do_activate`` vfuncs. Overriding those vfuncs segfaults during
    ``g_object_new`` on some AppImage PyGObject + bundled GLib stacks.
    """

    def __init__(self) -> None:
        self._store: SettingsStore | None = None
        self._setup_shown = False
        self._startup_complete = False
        super().__init__(
            application_id=config.APPLICATION_ID,
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )
        # After default Adw/Gtk Application startup has run.
        self.connect_after('startup', self._on_startup)
        self.connect('activate', self._on_activate)

    @property
    def store(self) -> SettingsStore:
        if self._store is None:
            self._store = SettingsStore()
        return self._store

    def _on_startup(self, *_args) -> None:
        # Register Adw widgets for Gtk.Template before any window is built.
        from ready2rip.window import ensure_adw_template_types

        ensure_adw_template_types()
        self._startup_complete = True
        GLib.set_application_name(config.APPLICATION_NAME)
        GLib.set_prgname(config.APPLICATION_ID)
        apply_color_scheme(self.store.get().color_scheme)
        try:
            register_application_icons()
        except Exception:
            pass
        self._setup_actions()
        self.set_menubar(None)

    def _on_activate(self, *_args) -> None:
        if not self._startup_complete:
            return
        from ready2rip.window import Ready2RipWindow

        win = self.props.active_window
        if not win:
            win = Ready2RipWindow(application=self, store=self.store)
            win.set_icon_name(config.APPLICATION_ID)
        win.present()
        if not self._setup_shown and needs_drive_setup(self.store):
            self._setup_shown = True
            # Block metadata immediately — probe may finish before the dialog.
            if isinstance(win, Ready2RipWindow):
                win.set_drive_setup_open(True)
            GLib.idle_add(self._deferred_drive_setup)

    def _deferred_drive_setup(self) -> bool:
        from ready2rip.window import Ready2RipWindow

        win = self.props.active_window
        if isinstance(win, Ready2RipWindow):
            win.present_drive_setup()
        return GLib.SOURCE_REMOVE

    def _setup_actions(self) -> None:
        self.create_action('quit', lambda *_: self.quit(), ['<primary>q'])
        self.create_action('about', self.on_about)
        self.create_action(
            'shortcuts',
            self.on_shortcuts,
            ['<primary>question', '<shift><primary>slash'],
        )

        self.set_accels_for_action('win.close', ['<primary>w'])
        self.set_accels_for_action('win.rip', ['<primary>r'])
        self.set_accels_for_action('win.eject', ['<primary>e'])
        self.set_accels_for_action('win.preferences', ['<primary>comma'])

    def build_primary_menu(self) -> Gio.Menu:
        """About menu: keyboard shortcuts and about."""
        menu = Gio.Menu()
        menu.append('Keyboard shortcuts', 'app.shortcuts')
        menu.append(f'About {config.APPLICATION_NAME}', 'app.about')
        return menu

    def on_shortcuts(self, *_args) -> None:
        present_keyboard_shortcuts(self.props.active_window)

    def on_about(self, *_args) -> None:
        about = Adw.AboutDialog(
            application_name=config.APPLICATION_NAME,
            application_icon=config.APPLICATION_ID,
            developer_name='gnostiko',
            version=config.APPLICATION_VERSION,
            developers=['gnostiko https://github.com/gnostiko'],
            copyright='© 2026 gnostiko',
            license_type=Gtk.License.GPL_3_0,
            comments=(
                'Secure CD ripper for Linux. ready2rip uses full cdparanoia, '
                'secure extraction, test and copy, cache defeat, AccurateRip, '
                'CTDB, MusicBrainz metadata, ReplayGain, and album art.'
            ),
            website='https://github.com/gnostiko/ready2rip',
            issue_url='https://github.com/gnostiko/ready2rip/issues',
        )
        about.present(self.props.active_window)

    def create_action(
        self, name: str, callback, shortcuts: list[str] | None = None
    ) -> None:
        action = Gio.SimpleAction.new(name, None)
        action.connect('activate', callback)
        self.add_action(action)
        if shortcuts:
            self.set_accels_for_action(f'app.{name}', shortcuts)
