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


def _running_from_appimage() -> bool:
    return bool(os.environ.get('APPIMAGE') or os.environ.get('APPDIR'))


def _appdir_icon_root() -> Path | None:
    """``$APPDIR/usr/share/icons`` when running from an AppImage / AppDir."""
    appdir = os.environ.get('APPDIR')
    if not appdir:
        return None
    root = Path(appdir) / 'usr' / 'share' / 'icons'
    if (root / 'hicolor').is_dir():
        return root
    return None


def _project_icon_root() -> Path | None:
    """``<repo>/data/icons`` when running from a source checkout."""
    # AppImage mounts live under /tmp/.mount_* — never walk into a host checkout.
    if _running_from_appimage():
        return None
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / 'data' / 'icons'
        if (candidate / 'hicolor').is_dir():
            return candidate
    return None


def _pkg_icon_root() -> Path | None:
    """Installed prefix icons (``$prefix/share/icons``), when present on disk."""
    if not config.PKGDATADIR:
        return None
    # Meson sets PKGDATADIR=/usr/share/ready2rip even inside AppImages; that
    # host path does not contain our icon. Prefer the AppDir copy instead.
    if _running_from_appimage():
        return None
    share = Path(config.PKGDATADIR).parent / 'icons'
    if (share / 'hicolor').is_dir():
        return share
    return None


def _icon_search_roots() -> list[Path]:
    roots: list[Path] = []
    seen: set[Path] = set()

    def add(path: Path | None) -> None:
        if path is None:
            return
        resolved = path.resolve()
        if resolved in seen or not path.is_dir():
            return
        seen.add(resolved)
        roots.append(path)

    env = os.environ.get('READY2RIP_ICON_DIR')
    if env:
        add(Path(env))
    add(_appdir_icon_root())
    add(_project_icon_root())
    add(_pkg_icon_root())
    return roots


def _app_icon_file(*, symbolic: bool = False) -> Path | None:
    """Path to the scalable or symbolic app icon."""
    if symbolic:
        names = (f'hicolor/symbolic/apps/{config.APPLICATION_ID}-symbolic.svg',)
    else:
        names = (
            f'hicolor/scalable/apps/{config.APPLICATION_ID}.svg',
            f'hicolor/128x128/apps/{config.APPLICATION_ID}.png',
            f'hicolor/256x256/apps/{config.APPLICATION_ID}.png',
            f'hicolor/symbolic/apps/{config.APPLICATION_ID}-symbolic.svg',
        )
    for root in _icon_search_roots():
        for rel in names:
            path = root / rel
            if path.is_file():
                return path
    # AppImage root symlink (linuxdeploy places <id>.svg next to AppRun).
    appdir = os.environ.get('APPDIR')
    if appdir and not symbolic:
        top = Path(appdir) / f'{config.APPLICATION_ID}.svg'
        if top.is_file():
            return top
    return None


def _install_user_app_icon(src: Path, *, symbolic: bool = False) -> None:
    """Copy one app icon file into the user hicolor tree."""
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
    elif src.suffix.lower() == '.png':
        # Keep raster installs under a common size so the shell finds them.
        dest_dir = (
            Path.home()
            / '.local'
            / 'share'
            / 'icons'
            / 'hicolor'
            / '128x128'
            / 'apps'
        )
        dest = dest_dir / f'{config.APPLICATION_ID}.png'
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


def _install_user_hicolor_from_root(src_root: Path) -> None:
    """Copy all packaged ``org.ready2rip.Ready2Rip*`` icons into ~/.local.

    GNOME Shell resolves the taskbar icon from the *user/system* icon theme,
    not from the AppImage's private ``XDG_DATA_DIRS``. Publishing into
    ``~/.local/share/icons/hicolor`` is what makes the open-app icon appear.
    """
    hicolor = src_root / 'hicolor'
    if not hicolor.is_dir():
        return
    dest_root = Path.home() / '.local' / 'share' / 'icons' / 'hicolor'
    prefix = config.APPLICATION_ID
    for path in hicolor.rglob('*'):
        if not path.is_file():
            continue
        stem_ok = path.stem == prefix or path.stem.startswith(f'{prefix}-')
        name_ok = path.name == prefix or path.name.startswith(f'{prefix}-')
        if not (stem_ok or name_ok):
            continue
        rel = path.relative_to(hicolor)
        dest = dest_root / rel
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.is_file() or dest.stat().st_mtime < path.stat().st_mtime:
                shutil.copy2(path, dest)
        except OSError as exc:
            log.debug('Could not install %s: %s', dest, exc)


def _desktop_exec_line() -> str:
    """Command for the user ``.desktop`` file."""
    appimage = os.environ.get('APPIMAGE')
    if appimage and Path(appimage).is_file():
        return f'"{appimage}"' if any(c in appimage for c in ' \t') else appimage

    appdir = os.environ.get('APPDIR')
    if appdir:
        apprun = Path(appdir) / 'AppRun'
        if apprun.is_file():
            text = str(apprun)
            return f'"{text}"' if any(c in text for c in ' \t') else text

    project = _project_icon_root()
    if project is not None:
        root = project.parent.parent  # data/icons → repo
        run = root / 'run'
        if run.is_file():
            return str(run)
        return f'env PYTHONPATH={root / "src"} python3 -m ready2rip.main'

    return 'ready2rip'


def _foreign_ready2rip_desktops(apps: Path) -> list[Path]:
    """Launchers installed by AppImage / Gear Lever (not our app-id file)."""
    ours = f'{config.APPLICATION_ID}.desktop'
    found: list[Path] = []
    try:
        candidates = list(apps.glob('*.desktop'))
    except OSError:
        return found
    for path in candidates:
        if path.name == ours:
            continue
        try:
            text = path.read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue
        # AppImage desktop integration, Gear Lever, appimaged, etc.
        if (
            f'StartupWMClass={config.APPLICATION_ID}' in text
            or 'X-AppImage-Name=ready2rip' in text
            or 'DESKTOPINTEGRATION=' in text
            or path.stem.lower() == 'ready2rip'
        ):
            found.append(path)
    return found


def _ensure_user_desktop_file() -> None:
    """Install a ``.desktop`` only when no AppImage/Gear Lever launcher exists.

    Writing ``org.ready2rip.Ready2Rip.desktop`` *and* keeping AppImage's
    ``ready2rip.desktop`` produces two identical entries in GNOME's app grid.
    Prefer the external integrator's launcher; fall back to our own for
    source / unpackaged runs.
    """
    apps = Path.home() / '.local' / 'share' / 'applications'
    apps.mkdir(parents=True, exist_ok=True)
    desktop = apps / f'{config.APPLICATION_ID}.desktop'

    foreign = _foreign_ready2rip_desktops(apps)
    if foreign:
        # Drop our duplicate so only one ready2rip appears in the overview.
        if desktop.is_file():
            try:
                desktop.unlink()
                log.debug(
                    'Removed duplicate desktop file %s (using %s)',
                    desktop,
                    foreign[0].name,
                )
            except OSError as exc:
                log.debug('Could not remove duplicate desktop file: %s', exc)
        return

    exec_line = _desktop_exec_line()
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
        'X-GNOME-UsesNotifications=false\n'
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
    roots = _icon_search_roots()

    # Publish every packaged size into ~/.local so the shell/taskbar can see it.
    for root in roots:
        _install_user_hicolor_from_root(root)

    # Belt-and-suspenders for scalable / symbolic singles.
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
    for root in roots:
        try:
            theme.add_search_path(str(root))
        except Exception as exc:
            log.debug('Could not add icon path %s: %s', root, exc)

    user_icons = Path.home() / '.local' / 'share' / 'icons'
    if user_icons.is_dir():
        try:
            theme.add_search_path(str(user_icons))
        except Exception as exc:
            log.debug('Could not add user icon path: %s', exc)

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
