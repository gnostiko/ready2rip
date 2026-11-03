# SPDX-License-Identifier: GPL-3.0-or-later
"""Application CSS for the main window (cover, tracks, progress)."""

from __future__ import annotations

import gi

gi.require_version('Gtk', '4.0')

from gi.repository import Gtk

# Adwaita ease-out cubic.
_EASE = 'cubic-bezier(0.33, 1, 0.68, 1)'


def window_css(*, cover_size: int) -> str:
    """Compact app CSS aligned with Adwaita spacing."""
    size = int(cover_size)
    return f"""
/* Artwork: plain titled column; Adwaita .card only on the cover. */
.ready2rip-art-group {{
    margin: 0;
}}
.ready2rip-art-group > label.heading {{
    /* Match AdwPreferencesGroup single-line header above the Album list. */
    font-weight: 600;
    min-height: 34px;
}}

/* Size only — .card supplies Adwaita rim + elevation without padding. */
.ready2rip-cover-frame {{
    min-width: {size}px;
    min-height: {size}px;
}}
.ready2rip-cover-frame.ready2rip-cover-empty {{
    background-color: alpha(@window_fg_color, 0.06);
}}
.ready2rip-cover-picture {{
    border-radius: 12px;
}}
image.ready2rip-cover-placeholder {{
    color: alpha(@window_fg_color, 0.35);
}}
label.ready2rip-cover-size {{
    line-height: 1.25;
}}

.ready2rip-rip-bar {{
    background-color: @window_bg_color;
    min-height: 48px;
}}

.ready2rip-flash-veil {{
    background-color: @success_bg_color;
    opacity: 0;
    transition: opacity 200ms {_EASE};
}}
.ready2rip-flash-veil.error {{
    background-color: @error_bg_color;
}}
.ready2rip-flash-veil.visible {{
    opacity: 0.34;
}}

button.ready2rip-setup-icon {{
    opacity: 0.55;
}}
button.ready2rip-setup-icon:hover {{
    opacity: 1;
}}

/* Cover action icons — visibility is cover_actions opacity (hover/focus). */
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
    min-width: 96px;
}}
.ready2rip-track-status {{
    min-width: 7em;
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


def banner_fill_css(fraction: float) -> str:
    """GNOME success-green fill for the Ripping disc banner."""
    percent = max(0.0, min(100.0, fraction * 100.0))
    if percent <= 0.05:
        return '.ready2rip-rip-bar { background-image: none; }'
    edge = max(0.0, percent - 2.5)
    return f"""
.ready2rip-rip-bar {{
    background-image: linear-gradient(
        to right,
        alpha(@success_bg_color, 0.72) 0%,
        alpha(@success_bg_color, 0.72) {edge:.1f}%,
        alpha(@success_bg_color, 0.28) {percent:.1f}%,
        transparent {percent:.1f}%
    );
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
