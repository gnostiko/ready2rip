# SPDX-License-Identifier: GPL-3.0-or-later
"""Register the application icon and resolve symbolic icon names safely."""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

from ready2rip import config

log = logging.getLogger(__name__)

# Preferred → fallback names for themes that rename Adwaita symbols.
ICON_FALLBACKS: dict[str, tuple[str, ...]] = {
    'open-menu-symbolic': (
        'open-menu-symbolic',
        'application-menu-symbolic',
        'view-more-symbolic',
    ),
    'media-eject-symbolic': (
        'media-eject-symbolic',
        'media-optical-symbolic',
    ),
    'media-playback-start-symbolic': (
        'media-playback-start-symbolic',
        'media-playback-start',
    ),
    'media-playback-pause-symbolic': (
        'media-playback-pause-symbolic',
        'media-playback-pause',
    ),
    'media-skip-backward-symbolic': (
        'media-skip-backward-symbolic',
        'media-seek-backward-symbolic',
        'go-previous-symbolic',
    ),
    'media-skip-forward-symbolic': (
        'media-skip-forward-symbolic',
        'media-seek-forward-symbolic',
        'go-next-symbolic',
    ),
    'folder-music-symbolic': (
        'folder-music-symbolic',
        'audio-x-generic-symbolic',
        'media-optical-symbolic',
        'emblem-music-symbolic',
    ),
    'media-optical-symbolic': (
        'media-optical-symbolic',
        'drive-optical-symbolic',
        'media-cdrom-symbolic',
    ),
    'drive-optical-symbolic': (
        'drive-optical-symbolic',
        'media-optical-symbolic',
        'drive-removable-media-symbolic',
    ),
    'audio-x-generic-symbolic': (
        'audio-x-generic-symbolic',
        'audio-x-generic',
        'folder-music-symbolic',
    ),
    'system-search-symbolic': (
        'system-search-symbolic',
        'edit-find-symbolic',
    ),
    'folder-symbolic': (
        'folder-symbolic',
        'folder',
    ),
    'folder-open-symbolic': (
        'folder-open-symbolic',
        'document-open-symbolic',
        'folder-symbolic',
    ),
    'user-trash-symbolic': (
        'user-trash-symbolic',
        'edit-delete-symbolic',
    ),
    'emblem-ok-symbolic': (
        'emblem-ok-symbolic',
        'object-select-symbolic',
        'emblem-default-symbolic',
    ),
    'dialog-warning-symbolic': (
        'dialog-warning-symbolic',
        'dialog-warning',
    ),
}


def _project_icon_root() -> Path | None:
    """``<repo>/data/icons`` when running from a source checkout."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / 'data' / 'icons'
        if (candidate / 'hicolor').is_dir():
            return candidate
    return None


def _icon_search_roots() -> list[Path]:
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
    return roots


def _app_icon_file(*, symbolic: bool = False) -> Path | None:
    """Path to the scalable or symbolic app icon."""
    if symbolic:
        names = (f'hicolor/symbolic/apps/{config.APPLICATION_ID}-symbolic.svg',)
    else:
        names = (
            f'hicolor/scalable/apps/{config.APPLICATION_ID}.svg',
            f'hicolor/symbolic/apps/{config.APPLICATION_ID}-symbolic.svg',
        )
    for root in _icon_search_roots():
        for rel in names:
            path = root / rel
            if path.is_file():
                return path
    return None


def _install_user_app_icon(src: Path, *, symbolic: bool = False) -> None:
    """Copy only our app icon into the user hicolor tree.

    Do **not** write ``index.theme`` — a partial user hicolor index shadows the
    system theme and breaks icons in this app and every other GTK/GNOME app.
    """
    if symbolic:
        dest_dir = (
            Path.home()
            / '.local'
            / 'share'
            / 'icons'
            / 'hicolor'
            / 'symbolic'
            / 'apps'
        )
        dest = dest_dir / f'{config.APPLICATION_ID}-symbolic.svg'
    else:
        dest_dir = (
            Path.home()
            / '.local'
            / 'share'
            / 'icons'
            / 'hicolor'
            / 'scalable'
            / 'apps'
        )
        dest = dest_dir / f'{config.APPLICATION_ID}.svg'
    dest_dir.mkdir(parents=True, exist_ok=True)
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
        run = root / 'run'
        if run.is_file():
            exec_line = str(run)
        else:
            exec_line = f'env PYTHONPATH={root / "src"} python3 -m ready2rip.main'
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


def resolve_icon_name(preferred: str, display=None) -> str:
    """Return the first icon name available on *display*, else *preferred*."""
    import gi

    gi.require_version('Gtk', '4.0')
    gi.require_version('Gdk', '4.0')
    from gi.repository import Gdk, Gtk

    if display is None:
        display = Gdk.Display.get_default()
    if display is None:
        return preferred
    theme = Gtk.IconTheme.get_for_display(display)
    candidates = ICON_FALLBACKS.get(preferred, (preferred,))
    for name in candidates:
        try:
            if theme.has_icon(name):
                return name
        except Exception:
            continue
    return preferred


def apply_icon(widget, preferred: str, *, display=None) -> str:
    """Set *widget*'s icon-name to a resolvable name; return the name used."""
    name = resolve_icon_name(preferred, display=display)
    if hasattr(widget, 'set_icon_name'):
        widget.set_icon_name(name)
    elif hasattr(widget, 'set_from_icon_name'):
        widget.set_from_icon_name(name)
    return name


def register_application_icons() -> None:
    """Publish the app icon and set the default window icon name.

    Safe for the rest of the desktop: never replaces ``hicolor/index.theme``.
    """
    import gi

    gi.require_version('Gtk', '4.0')
    gi.require_version('Gdk', '4.0')
    from gi.repository import Gdk, Gtk

    icon_name = config.APPLICATION_ID
    scalable = _app_icon_file(symbolic=False)
    if scalable is not None:
        _install_user_app_icon(scalable, symbolic=False)
    symbolic = _app_icon_file(symbolic=True)
    if symbolic is not None:
        _install_user_app_icon(symbolic, symbolic=True)
    _ensure_user_desktop_file()

    display = Gdk.Display.get_default()
    if display is None:
        return

    theme = Gtk.IconTheme.get_for_display(display)

    # Development: search the project icon tree (contains hicolor/).
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
