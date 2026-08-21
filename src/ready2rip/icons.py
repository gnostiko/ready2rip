# SPDX-License-Identifier: GPL-3.0-or-later
"""Register the application icon without disturbing the system icon theme."""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

from ready2rip import config

log = logging.getLogger(__name__)


def _project_icon_root() -> Path | None:
    """``<repo>/data/icons`` when running from a source checkout."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / 'data' / 'icons'
        if (candidate / 'hicolor').is_dir():
            return candidate
    return None


def _app_icon_file() -> Path | None:
    """Path to the default (scalable) app icon SVG."""
    # Prefer scalable (already the symbolic artwork), then symbolic source.
    names = (
        f'hicolor/scalable/apps/{config.APPLICATION_ID}.svg',
        f'hicolor/symbolic/apps/{config.APPLICATION_ID}-symbolic.svg',
    )
    roots: list[Path] = []
    env = os.environ.get('READY2RIP_ICON_DIR')
    if env:
        roots.append(Path(env))
    project = _project_icon_root()
    if project is not None:
        roots.append(project)
    if config.PKGDATADIR:
        share = Path(config.PKGDATADIR).parent / 'icons'
        if share.is_dir():
            roots.append(share)
    for root in roots:
        for rel in names:
            path = root / rel
            if path.is_file():
                return path
    return None


def _install_user_app_icon(src: Path) -> None:
    """Copy only our app icon into the user hicolor tree.

    Do **not** write ``index.theme`` — a partial user hicolor index shadows the
    system theme and breaks icons in this app and every other GTK/GNOME app.
    """
    dest_dir = (
        Path.home()
        / '.local'
        / 'share'
        / 'icons'
        / 'hicolor'
        / 'scalable'
        / 'apps'
    )
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f'{config.APPLICATION_ID}.svg'
    try:
        if not dest.is_file() or dest.stat().st_mtime < src.stat().st_mtime:
            shutil.copy2(src, dest)
            log.debug('Installed user app icon %s', dest)
    except OSError as exc:
        log.debug('Could not install user app icon: %s', exc)


def _ensure_user_desktop_file() -> None:
    """Install a ``.desktop`` so GNOME can map the running app id to an icon."""
    apps = Path.home() / '.local' / 'share' / 'applications'
    apps.mkdir(parents=True, exist_ok=True)
    desktop = apps / f'{config.APPLICATION_ID}.desktop'

    project = _project_icon_root()
    if project is not None:
        root = project.parent.parent  # data/icons → repo
        exec_line = (
            f'env PYTHONPATH={root / "src"} '
            f'GSETTINGS_SCHEMA_DIR={root / "build" / "data"} '
            f'python3 -m ready2rip.main'
        )
    else:
        exec_line = 'ready2rip'

    body = (
        '[Desktop Entry]\n'
        f'Name={config.APPLICATION_NAME}\n'
        'Comment=Secure CD ripper for Linux\n'
        f'Exec={exec_line}\n'
        f'Icon={config.APPLICATION_ID}\n'
        'Terminal=false\n'
        'Type=Application\n'
        'Categories=AudioVideo;Audio;Music;\n'
        'StartupNotify=true\n'
        f'StartupWMClass={config.APPLICATION_ID}\n'
    )
    try:
        if not desktop.is_file() or desktop.read_text(encoding='utf-8') != body:
            desktop.write_text(body, encoding='utf-8')
    except OSError as exc:
        log.debug('Could not write desktop file: %s', exc)


def register_application_icons() -> None:
    """Publish the app icon and set the default window icon name.

    Safe for the rest of the desktop: never replaces ``hicolor/index.theme``.
    """
    import gi

    gi.require_version('Gtk', '4.0')
    gi.require_version('Gdk', '4.0')
    from gi.repository import Gdk, Gtk

    icon_name = config.APPLICATION_ID
    icon_file = _app_icon_file()
    if icon_file is not None:
        _install_user_app_icon(icon_file)
    _ensure_user_desktop_file()

    display = Gdk.Display.get_default()
    if display is None:
        return

    theme = Gtk.IconTheme.get_for_display(display)

    # Only add the *project* icon dir for development lookups — not the whole
    # user icons tree (that is already part of the normal XDG theme search).
    project = _project_icon_root()
    if project is not None:
        try:
            theme.add_search_path(str(project))
        except Exception as exc:
            log.debug('Could not add icon path %s: %s', project, exc)

    try:
        theme.rescan_if_needed()
    except Exception:
        pass

    try:
        Gtk.Window.set_default_icon_name(icon_name)
    except Exception as exc:
        log.debug('set_default_icon_name failed: %s', exc)

    if not theme.has_icon(icon_name):
        log.warning(
            'Application icon %r not found. Window/About icon may be missing '
            'until icons are installed.',
            icon_name,
        )
