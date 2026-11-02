# SPDX-License-Identifier: GPL-3.0-or-later
"""Adwaita color scheme helpers."""

from __future__ import annotations

from typing import Literal

import gi

gi.require_version('Adw', '1')

from gi.repository import Adw

ColorSchemeName = Literal['default', 'light', 'dark']
COLOR_SCHEMES: tuple[ColorSchemeName, ...] = ('default', 'light', 'dark')


def normalize_color_scheme(scheme: str) -> ColorSchemeName:
    if scheme in COLOR_SCHEMES:
        return scheme  # type: ignore[return-value]
    return 'default'


def apply_color_scheme(scheme: str) -> ColorSchemeName:
    """Map settings string → Adw.StyleManager color scheme."""
    scheme = normalize_color_scheme(scheme)
    if scheme == 'light':
        color = Adw.ColorScheme.FORCE_LIGHT
    elif scheme == 'dark':
        color = Adw.ColorScheme.FORCE_DARK
    else:
        color = Adw.ColorScheme.DEFAULT
    Adw.StyleManager.get_default().set_color_scheme(color)
    return scheme
