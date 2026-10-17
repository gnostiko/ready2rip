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
from ready2rip.setup_dialog import DriveSetupDialog
from ready2rip.shortcuts import present_keyboard_shortcuts
from ready2rip.theme import (
    COLOR_SCHEME_LABELS,
    COLOR_SCHEMES,
    apply_color_scheme,
    normalize_color_scheme,
)
from ready2rip.window import Ready2RipWindow


class Application(Adw.Application):
    """Main application object."""

    def __init__(self) -> None:
        super().__init__(
            application_id=config.APPLICATION_ID,
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )
        self.store = SettingsStore()
        self._setup_shown = False
        self._color_action: Gio.SimpleAction | None = None

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        GLib.set_application_name(config.APPLICATION_NAME)
        GLib.set_prgname(config.APPLICATION_ID)
        apply_color_scheme(self.store.get().color_scheme)
        try:
            register_application_icons()
        except Exception:
            pass
        self._setup_actions()
        self.set_menubar(None)

    def do_activate(self) -> None:
        win = self.props.active_window
        if not win:
            win = Ready2RipWindow(application=self, store=self.store)
            win.set_icon_name(config.APPLICATION_ID)
        win.present()
        if not self._setup_shown and needs_drive_setup(self.store):
            self._setup_shown = True
            GLib.timeout_add(400, self._deferred_drive_setup)

    def _deferred_drive_setup(self) -> bool:
        self._present_drive_setup()
        return GLib.SOURCE_REMOVE

    def _setup_actions(self) -> None:
        self.create_action('quit', lambda *_: self.quit(), ['<primary>q'])
        self.create_action('about', self.on_about)
        self.create_action(
            'shortcuts',
            self.on_shortcuts,
            ['<primary>question', '<shift><primary>slash'],
        )

        scheme = normalize_color_scheme(self.store.get().color_scheme)
        color = Gio.SimpleAction.new_stateful(
            'color-scheme',
            GLib.VariantType.new('s'),
            GLib.Variant.new_string(scheme),
        )
        color.connect('activate', self.on_color_scheme)
        self.add_action(color)
        self._color_action = color

        self.set_accels_for_action('win.close', ['<primary>w'])
        self.set_accels_for_action('win.rip', ['<primary>r'])
        self.set_accels_for_action('win.eject', ['<primary>e'])
        self.set_accels_for_action('win.preferences', ['<primary>comma'])
        self.set_accels_for_action('win.play-toggle', ['<primary>p', 'AudioPlay'])
        self.set_accels_for_action('win.play-next', ['AudioNext', '<alt>Right'])
        self.set_accels_for_action('win.play-previous', ['AudioPrev', '<alt>Left'])

    def build_primary_menu(self) -> Gio.Menu:
        """Hamburger menu: settings, appearance, shortcuts, about."""
        menu = Gio.Menu()

        prefs = Gio.Menu()
        prefs.append('Settings', 'win.preferences')
        menu.append_section(None, prefs)

        appearance = Gio.Menu()
        for key in COLOR_SCHEMES:
            appearance.append(COLOR_SCHEME_LABELS[key], f'app.color-scheme::{key}')
        menu.append_section('Appearance', appearance)

        help_section = Gio.Menu()
        help_section.append('Keyboard Shortcuts', 'app.shortcuts')
        help_section.append(f'About {config.APPLICATION_NAME}', 'app.about')
        menu.append_section(None, help_section)
        return menu

    def on_color_scheme(self, action: Gio.SimpleAction, param: GLib.Variant | None) -> None:
        if param is None:
            return
        scheme = normalize_color_scheme(param.get_string())
        action.set_state(GLib.Variant.new_string(scheme))
        self.store.update(color_scheme=scheme)
        apply_color_scheme(scheme)

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
                'Secure CD ripper for Linux. Ready2Rip uses full cdparanoia '
                'secure extraction, test & copy, cache defeat, AccurateRip, '
                'MusicBrainz metadata, ReplayGain, and album art.'
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
