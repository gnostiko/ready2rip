# SPDX-License-Identifier: GPL-3.0-or-later
"""Application CSS for the main window (cover, tracks, progress, buttons)."""

from __future__ import annotations

import gi

gi.require_version('Gtk', '4.0')

from gi.repository import Gtk


def window_css(*, cover_size: int) -> str:
    """Compact app CSS; *cover_size* sizes the album-art frame."""
    size = int(cover_size)
    return f"""
.ready2rip-art-info {{
    opacity: 0.8;
}}

/* Art preferences group: bleed cover to the card edge */
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
}}
.ready2rip-cover-picture {{
    border-radius: 12px;
}}
image.ready2rip-cover-placeholder {{
    color: alpha(@window_fg_color, 0.4);
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
    min-width: 36px;
    min-height: 36px;
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

.ready2rip-track-row {{
    padding: 10px 12px;
    min-height: 44px;
}}
.ready2rip-track-header {{
    opacity: 0.9;
}}
.ready2rip-track-header .ready2rip-track-row {{
    padding-top: 6px;
    padding-bottom: 4px;
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
.ready2rip-track-dur {{
    min-width: 3em;
    font-feature-settings: "tnum";
    opacity: 0.65;
}}
entry.ready2rip-track-entry {{
    min-height: 34px;
}}
"""


def install_window_styles(
    display: Gtk.Display, *, cover_size: int
) -> Gtk.CssProvider:
    """Install application-priority CSS for *display*."""
    provider = Gtk.CssProvider()
    provider.load_from_string(window_css(cover_size=cover_size))
    Gtk.StyleContext.add_provider_for_display(
        display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )
    return provider
