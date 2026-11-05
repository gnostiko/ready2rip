# SPDX-License-Identifier: GPL-3.0-or-later
"""Application CSS for the main window (cover, tracks, progress)."""

from __future__ import annotations

import gi

gi.require_version('Gtk', '4.0')

from gi.repository import Gtk

# Adwaita ease-out cubic.
_EASE = 'cubic-bezier(0.33, 1, 0.68, 1)'


def window_css() -> str:
    """Compact app CSS aligned with Adwaita spacing.

    Cover size is set via widget size_request, not CSS, so resize does not
    force a stylesheet reload.
    """
    return f"""
.ready2rip-art-group {{
    margin: 0;
}}
.ready2rip-art-group list.boxed-list {{
    background-color: transparent;
    box-shadow: none;
    border-radius: 0;
}}
.ready2rip-art-group row {{
    padding: 0;
    min-height: 0;
    background-color: transparent;
    box-shadow: none;
    border: none;
}}
.ready2rip-art-group row:hover,
.ready2rip-art-group row:active,
.ready2rip-art-group row:selected {{
    background-color: transparent;
    box-shadow: none;
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
label.ready2rip-cover-bubble {{
    background-color: alpha(black, 0.55);
    color: white;
    border-radius: 999px;
    padding: 3px 10px;
    min-height: 22px;
}}
.ready2rip-album-locked {{
    opacity: 1;
}}
.ready2rip-album-group row {{
    min-height: 36px;
}}
.ready2rip-album-group row entry {{
    min-height: 28px;
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
.ready2rip-track-isrc {{
    min-width: 7.5em;
    max-width: 9em;
    font-feature-settings: "tnum";
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
    """GNOME success-green left-to-right fill for rip + offset banners."""
    selector = '.ready2rip-rip-bar'
    percent = max(0.0, min(100.0, fraction * 100.0))
    # Show even early sub-percent fill so the first track does not look stuck.
    if percent <= 0.001:
        return f'{selector} {{ background-image: none; }}'
    # Soft edge scales down for tiny fills so early motion stays visible.
    soft = min(2.5, max(0.15, percent * 0.35))
    edge = max(0.0, percent - soft)
    return f"""
{selector} {{
    background-image: linear-gradient(
        to right,
        alpha(@success_bg_color, 0.72) 0%,
        alpha(@success_bg_color, 0.72) {edge:.2f}%,
        alpha(@success_bg_color, 0.28) {percent:.2f}%,
        transparent {percent:.2f}%
    );
}}
"""



_window_provider: Gtk.CssProvider | None = None


def install_window_styles(display: Gtk.Display) -> Gtk.CssProvider:
    """Install application-priority CSS for *display* (once per process)."""
    global _window_provider
    if _window_provider is not None:
        return _window_provider
    provider = Gtk.CssProvider()
    provider.load_from_string(window_css())
    Gtk.StyleContext.add_provider_for_display(
        display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )
    _window_provider = provider
    return provider
