# SPDX-License-Identifier: GPL-3.0-or-later
"""Register application icons for installed and development runs."""

from __future__ import annotations

import logging
from pathlib import Path

from ready2rip import config

log = logging.getLogger(__name__)


def _candidate_icon_roots() -> list[Path]:
    """Return directories that may contain a ``hicolor/`` icon theme tree."""
    roots: list[Path] = []

    # Development: <project>/data/icons  (src/ready2rip/icons.py → project root)
    here = Path(__file__).resolve()
    # .../src/ready2rip/icons.py → parents[2] == project root when layout is src/ready2rip
    for parent in here.parents:
        candidate = parent / 'data' / 'icons'
        if (candidate / 'hicolor').is_dir():
            roots.append(candidate)
            break

    # Optional env override (e.g. tests)
    import os

    env = os.environ.get('READY2RIP_ICON_DIR')
    if env:
        roots.insert(0, Path(env))

    # Meson install puts icons under $prefix/share/icons (system theme path).
    # If PKGDATADIR is set, also try adjacent icons dir.
    if config.PKGDATADIR:
        pkg = Path(config.PKGDATADIR)
        # /usr/share/ready2rip → /usr/share/icons
        share = pkg.parent
        if (share / 'icons' / 'hicolor').is_dir():
            roots.append(share / 'icons')

    # De-dupe while preserving order
    seen: set[Path] = set()
    unique: list[Path] = []
    for r in roots:
        try:
            key = r.resolve()
        except OSError:
            key = r
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return unique


def register_application_icons() -> None:
    """Add icon search paths and set the default window icon name.

    Call once after GTK is initialized (e.g. from ``Application.do_startup``).
    The desktop file / About dialog use ``org.ready2rip.Ready2Rip``, which maps
    to::

        data/icons/hicolor/scalable/apps/org.ready2rip.Ready2Rip.svg
        data/icons/hicolor/symbolic/apps/org.ready2rip.Ready2Rip-symbolic.svg
    """
    import gi

    gi.require_version('Gtk', '4.0')
    gi.require_version('Gdk', '4.0')
    from gi.repository import Gdk, Gtk  # noqa: E402

    icon_name = config.APPLICATION_ID
    display = Gdk.Display.get_default()
    if display is None:
        return

    theme = Gtk.IconTheme.get_for_display(display)
    for root in _candidate_icon_roots():
        if not (root / 'hicolor').is_dir():
            continue
        path = str(root)
        try:
            theme.add_search_path(path)
            log.debug('Added icon search path: %s', path)
        except Exception as exc:  # noqa: BLE001
            log.debug('Could not add icon path %s: %s', path, exc)

    # Default for all windows + task switcher when the theme resolves the name.
    try:
        Gtk.Window.set_default_icon_name(icon_name)
    except Exception as exc:  # noqa: BLE001
        log.debug('set_default_icon_name failed: %s', exc)

    if not theme.has_icon(icon_name):
        log.warning(
            'Application icon %r not found in icon theme '
            '(searched %s). About / window icon may be missing until install.',
            icon_name,
            [str(p) for p in _candidate_icon_roots()],
        )
