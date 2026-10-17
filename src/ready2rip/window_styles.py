# SPDX-License-Identifier: GPL-3.0-or-later
"""Application CSS for the main window (cover, tracks, progress, buttons)."""

from __future__ import annotations

import gi

gi.require_version('Gtk', '4.0')

from gi.repository import Gtk


def window_css(*, cover_size: int) -> str:
    """Compact app CSS aligned with Adwaita spacing."""
    size = int(cover_size)
    return f"""
/* Art group: bleed cover to the card edge (no list chrome). */
.ready2rip-art-group list {{
    background: transparent;
    border: none;
    box-shadow: none;
    border-radius: 12px;
    padding: 0;
}}
.ready2rip-art-group row,
.ready2rip-art-row {{
    padding: 0;
    min-height: 0;
    border: none;
    background: transparent;
    box-shadow: none;
}}
.ready2rip-art-group row:hover {{
    background: transparent;
}}

.ready2rip-cover-frame {{
    min-width: {size}px;
    min-height: {size}px;
    border-radius: 12px;
    background-color: alpha(@window_fg_color, 0.06);
    /* Adwaita card elevation — soft rim + short drop shadow. */
    box-shadow:
        0 0 0 1px alpha(black, 0.06),
        0 1px 2px alpha(black, 0.08),
        0 3px 8px alpha(black, 0.10);
}}
.ready2rip-cover-picture {{
    border-radius: 12px;
}}
image.ready2rip-cover-placeholder {{
    color: alpha(@window_fg_color, 0.35);
}}

.ready2rip-rip-bar {{
    background-color: @window_bg_color;
}}

progressbar.ready2rip-progress,
progressbar.ready2rip-progress > trough,
progressbar.ready2rip-progress > trough > progress {{
    min-height: 4px;
    border-radius: 9999px;
}}
progressbar.ready2rip-progress.success > trough > progress {{
    background-color: @success_color;
}}
progressbar.ready2rip-progress.error > trough > progress {{
    background-color: @error_color;
}}

button.ready2rip-setup-icon {{
    opacity: 0.55;
}}
button.ready2rip-setup-icon:hover {{
    opacity: 1;
}}

button.ready2rip-cover-button {{
    min-width: 32px;
    min-height: 32px;
    padding: 0;
}}
button.ready2rip-cover-trash,
button.ready2rip-cover-trash:hover,
button.ready2rip-cover-trash:active {{
    background-color: @destructive_bg_color;
    color: @destructive_fg_color;
    border: none;
    box-shadow: none;
}}
button.ready2rip-cover-trash:disabled {{
    opacity: 0.4;
}}

/* Raised circular transport “bubbles” (not flat). */
button.ready2rip-transport {{
    min-width: 48px;
    min-height: 48px;
    padding: 0;
    border-radius: 9999px;
    background-color: alpha(@window_fg_color, 0.08);
    box-shadow: 0 1px 2px alpha(@window_fg_color, 0.12);
}}
button.ready2rip-transport:hover {{
    background-color: alpha(@window_fg_color, 0.14);
}}
button.ready2rip-transport:disabled {{
    opacity: 0.4;
}}
button.ready2rip-transport-play {{
    min-width: 64px;
    min-height: 64px;
}}
button.ready2rip-transport-play.suggested-action {{
    box-shadow: 0 2px 6px alpha(@accent_bg_color, 0.45);
}}

/* Keep seek sizing on the scale itself — overriding trough/slider min-*
   makes GtkGizmo report negative min width/height. */
scale.ready2rip-seek {{
    min-width: 120px;
    padding-top: 6px;
    padding-bottom: 6px;
}}

/* Match AdwActionRow rhythm inside PreferencesGroup lists. */
.ready2rip-track-row {{
    padding: 10px 12px;
    min-height: 44px;
}}
.ready2rip-track-header {{
    opacity: 0.85;
}}
.ready2rip-track-header .ready2rip-track-row {{
    padding-top: 6px;
    padding-bottom: 6px;
    min-height: 28px;
}}
.ready2rip-track-lead {{
    min-width: 3.6em;
}}
.ready2rip-track-num {{
    min-width: 2em;
    font-feature-settings: "tnum";
    opacity: 0.7;
}}
.ready2rip-track-artist {{
    min-width: 160px;
}}
.ready2rip-track-dur {{
    min-width: 3em;
    font-feature-settings: "tnum";
    opacity: 0.65;
}}
entry.ready2rip-track-entry {{
    min-height: 34px;
}}
"""


_window_provider: Gtk.CssProvider | None = None
_window_cover_size: int | None = None


def install_window_styles(
    display: Gtk.Display, *, cover_size: int
) -> Gtk.CssProvider:
    """Install application-priority CSS for *display* (once per process)."""
    global _window_provider, _window_cover_size
    css = window_css(cover_size=cover_size)
    if _window_provider is not None:
        if _window_cover_size != cover_size:
            _window_provider.load_from_string(css)
            _window_cover_size = cover_size
        return _window_provider
    provider = Gtk.CssProvider()
    provider.load_from_string(css)
    Gtk.StyleContext.add_provider_for_display(
        display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )
    _window_provider = provider
    _window_cover_size = cover_size
    return provider
