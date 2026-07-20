# SPDX-License-Identifier: GPL-3.0-or-later
"""Adwaita Application subclass."""

from __future__ import annotations

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from ready2rip import config  # noqa: E402
from ready2rip.drive_setup import needs_drive_setup  # noqa: E402
from ready2rip.icons import register_application_icons  # noqa: E402
from ready2rip.settings import SettingsStore  # noqa: E402
from ready2rip.setup_dialog import DriveSetupDialog  # noqa: E402
from ready2rip.window import Ready2RipWindow  # noqa: E402


class Application(Adw.Application):
    """Main application object."""

    def __init__(self) -> None:
        super().__init__(
            application_id=config.APPLICATION_ID,
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )
        self.store = SettingsStore()
        self._setup_shown = False
        self.create_action('quit', lambda *_: self.quit(), ['<primary>q'])
        self.create_action('about', self.on_about)

    def do_activate(self) -> None:
        win = self.props.active_window
        if not win:
            win = Ready2RipWindow(application=self, store=self.store)
            # Ensure the window uses the app icon (task switcher / alt-tab).
            win.set_icon_name(config.APPLICATION_ID)
        win.present()
        # First-run (or drive-changed) offset setup — once per activate session.
        if not self._setup_shown and needs_drive_setup(self.store):
            self._setup_shown = True
            self._present_drive_setup()

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        # App name for shell / about; prgname matches FreeDesktop app id.
        GLib.set_application_name(config.APPLICATION_NAME)
        GLib.set_prgname(config.APPLICATION_ID)
        register_application_icons()

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
                'Rip audio CDs with cdparanoia, AccurateRip, MusicBrainz, '
                'ReplayGain, and album art.'
            ),
            website='https://github.com/gnostiko/ready2rip',
            issue_url='https://github.com/gnostiko/ready2rip/issues',
        )
        about.present(self.props.active_window)

    def _present_drive_setup(self) -> None:
        dialog = DriveSetupDialog(self.store)

        def on_closed(*_a) -> None:
            win = self.props.active_window
            if isinstance(win, Ready2RipWindow):
                win.sync_options_from_store()

        dialog.connect('closed', on_closed)
        dialog.present(self.props.active_window)

    def create_action(
        self, name: str, callback, shortcuts: list[str] | None = None
    ) -> None:
        action = Gio.SimpleAction.new(name, None)
        action.connect('activate', callback)
        self.add_action(action)
        if shortcuts:
            self.set_accels_for_action(f'app.{name}', shortcuts)
