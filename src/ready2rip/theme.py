# SPDX-License-Identifier: GPL-3.0-or-later
"""Appearance: Adwaita color scheme + flat chrome surface colours."""

from __future__ import annotations

from typing import Literal

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gtk  # noqa: E402

ColorSchemeName = Literal['default', 'light', 'dark']
COLOR_SCHEMES: tuple[ColorSchemeName, ...] = ('default', 'light', 'dark')

COLOR_SCHEME_ICONS: dict[str, str] = {
    'default': 'display-brightness-symbolic',
    'light': 'weather-clear-symbolic',
    'dark': 'weather-clear-night-symbolic',
}
COLOR_SCHEME_LABELS: dict[str, str] = {
    'default': 'System',
    'light': 'Light',
    'dark': 'Dark',
}

# Flat chrome: pure black dark; warm grey light.
# Cards use the same relative % lift as light (#D2D1CD → #DCDBD7 ≈ +4.8%).
CHROME_DARK_BASE = '#000000'
CHROME_LIGHT_BASE = '#D2D1CD'
CHROME_LIGHT_RAISED = '#DCDBD7'

# Surfaces that share the window/base colour vs raised card colour.
_BASE_VARS = (
    'window_bg_color',
    'view_bg_color',
    'headerbar_bg_color',
    'headerbar_backdrop_color',
    'sidebar_bg_color',
    'secondary_sidebar_bg_color',
    'dialog_bg_color',
)
_RAISED_VARS = (
    'popover_bg_color',
    'card_bg_color',
    'thumbnail_bg_color',
)


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


def next_color_scheme(scheme: str) -> ColorSchemeName:
    current = normalize_color_scheme(scheme)
    idx = COLOR_SCHEMES.index(current)
    return COLOR_SCHEMES[(idx + 1) % len(COLOR_SCHEMES)]


def parse_hex_rgb(color: str) -> tuple[int, int, int]:
    h = color.removeprefix('#').strip()
    if len(h) != 6:
        raise ValueError(f'expected #RRGGBB, got {color!r}')
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def chrome_raised_for_base(
    base: str, *, ref_base: str, ref_raised: str
) -> str:
    """Raise *base* by the same per-channel percentage as ref_base→ref_raised."""
    br, bg, bb = parse_hex_rgb(base)
    r0, g0, b0 = parse_hex_rgb(ref_base)
    r1, g1, b1 = parse_hex_rgb(ref_raised)

    def channel(b: int, a0: int, a1: int) -> int:
        if a0 <= 0:
            return b
        pct = (a1 - a0) / a0
        if b <= 0:
            return max(0, min(255, int(round(pct * 255))))
        return max(0, min(255, int(round(b * (1.0 + pct)))))

    r, g, b = channel(br, r0, r1), channel(bg, g0, g1), channel(bb, b0, b1)
    return f'#{r:02x}{g:02x}{b:02x}'


def chrome_palette() -> tuple[str, str, str, bool]:
    """Return (base, raised, shade_css, is_dark)."""
    dark = bool(Adw.StyleManager.get_default().get_dark())
    if dark:
        base = CHROME_DARK_BASE
        raised = chrome_raised_for_base(
            base, ref_base=CHROME_LIGHT_BASE, ref_raised=CHROME_LIGHT_RAISED
        )
        # ~28% → Adwaita dimming ≈ 56% scrim on pure black.
        return base, raised, 'rgb(0 0 0 / 28%)', True
    return CHROME_LIGHT_BASE, CHROME_LIGHT_RAISED, 'rgb(0 0 0 / 12%)', False


def chrome_css(base: str, raised: str, shade: str, *, dark: bool) -> str:
    """Minimal Adwaita surface + dialog-dim overrides."""
    defines = '\n'.join(
        f'@define-color {name} {base};' for name in _BASE_VARS
    )
    defines += '\n' + '\n'.join(
        f'@define-color {name} {raised};' for name in _RAISED_VARS
    )
    defines += f'\n@define-color shade_color {shade};'

    # CSS custom props (libadwaita) — underscore names → kebab-case.
    props = '\n'.join(
        f'    --{name.replace("_", "-")}: {base};' for name in _BASE_VARS
    )
    props += '\n' + '\n'.join(
        f'    --{name.replace("_", "-")}: {raised};' for name in _RAISED_VARS
    )
    props += f'\n    --shade-color: {shade};'

    dim = '55%' if dark else '24%'
    return f"""
{defines}

:root, window.ready2rip-window {{
{props}
}}

floating-sheet > dimming, .dimming {{
    background-color: rgb(0 0 0 / {dim});
}}
"""


class ChromeStyleController:
    """Keeps window chrome in sync with Adw dark/light changes."""

    def __init__(self, window: Gtk.Widget) -> None:
        self._window = window
        self._provider: Gtk.CssProvider | None = None
        window.add_css_class('ready2rip-window')
        Adw.StyleManager.get_default().connect(
            'notify::dark', self._on_dark_changed
        )
        self.sync()

    def _on_dark_changed(self, *_args) -> None:
        self.sync()

    def sync(self) -> None:
        display = self._window.get_display()
        if self._provider is not None:
            Gtk.StyleContext.remove_provider_for_display(
                display, self._provider
            )
            self._provider = None
        base, raised, shade, dark = chrome_palette()
        provider = Gtk.CssProvider()
        provider.load_from_string(
            chrome_css(base, raised, shade, dark=dark)
        )
        Gtk.StyleContext.add_provider_for_display(
            display, provider, Gtk.STYLE_PROVIDER_PRIORITY_USER
        )
        self._provider = provider
