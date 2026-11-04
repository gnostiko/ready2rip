# SPDX-License-Identifier: GPL-3.0-or-later
"""Multi-artist helpers (semicolon storage + display styles).

In the UI and canonical string form, multiple artists are stored as::

    Artist One; Artist Two; Artist Three

Filename styles (settings):
  * ``semicolon`` — ``A; B; C``
  * ``slash`` — ``A / B / C``
  * ``feat`` — ``Song feat. B & C`` in the title; primary artist only in tags

Embedded tags always use native multi-value artist fields (Vorbis / ID3 / MP4),
except ``feat`` which writes the primary artist and puts guests on the title.
"""

from __future__ import annotations

# Multi-value display / entry separator (semicolon + space).
ARTIST_SEPARATOR = '; '

ARTIST_STYLE_SEMICOLON = 'semicolon'
ARTIST_STYLE_SLASH = 'slash'
ARTIST_STYLE_FEAT = 'feat'
DEFAULT_ARTIST_STYLE = ARTIST_STYLE_SEMICOLON

ARTIST_STYLES: tuple[tuple[str, str], ...] = (
    (ARTIST_STYLE_SEMICOLON, 'Semicolon ;'),
    (ARTIST_STYLE_SLASH, 'Slash /'),
    (ARTIST_STYLE_FEAT, 'feat.'),
)


def split_artists(value: str | None) -> list[str]:
    """Split a multi-artist string on ``;`` into non-empty names.

    Accepts ``A; B``, ``A;B``, and trims whitespace. Empty segments are dropped.
    """
    if not value:
        return []
    parts = [p.strip() for p in str(value).split(';')]
    return [p for p in parts if p]


def join_artists(artists: list[str] | tuple[str, ...] | str | None) -> str:
    """Join artist names with ``; `` for storage in metadata strings."""
    if artists is None:
        return ''
    if isinstance(artists, str):
        return join_artists(split_artists(artists))
    cleaned = [a.strip() for a in artists if a and str(a).strip()]
    return ARTIST_SEPARATOR.join(cleaned)


def normalize_artists(value: str | None) -> str:
    """Canonical multi-artist form: ``Name; Name`` (no empty segments)."""
    return join_artists(split_artists(value))


def join_names_natural(names: list[str] | tuple[str, ...]) -> str:
    """Join names with commas and a final ``&`` (``A``, ``A & B``, ``A, B & C``)."""
    cleaned = [n.strip() for n in names if n and str(n).strip()]
    if not cleaned:
        return ''
    if len(cleaned) == 1:
        return cleaned[0]
    if len(cleaned) == 2:
        return f'{cleaned[0]} & {cleaned[1]}'
    return f'{", ".join(cleaned[:-1])} & {cleaned[-1]}'


def normalize_artist_style(style: str | None) -> str:
    """Clamp *style* to a known artist display style id."""
    text = (style or '').strip().lower()
    allowed = {sid for sid, _label in ARTIST_STYLES}
    if text in allowed:
        return text
    return DEFAULT_ARTIST_STYLE


def format_artists(
    value: str | None,
    style: str | None = DEFAULT_ARTIST_STYLE,
) -> str:
    """Format a semicolon-stored artist string for display / filenames."""
    names = split_artists(value)
    if not names:
        return ''
    kind = normalize_artist_style(style)
    if kind == ARTIST_STYLE_SLASH:
        return ' / '.join(names)
    if kind == ARTIST_STYLE_FEAT:
        if len(names) == 1:
            return names[0]
        return f'{names[0]} feat. {join_names_natural(names[1:])}'
    return join_artists(names)


def format_title_with_artists(
    title: str | None,
    artist: str | None,
    style: str | None = DEFAULT_ARTIST_STYLE,
) -> str:
    """Append ``feat.`` guests to *title* when style is ``feat`` and guests exist.

    Example: ``Song`` + ``Main; Guest; Other`` → ``Song feat. Guest & Other``.
    """
    text = (title or '').strip()
    if normalize_artist_style(style) != ARTIST_STYLE_FEAT:
        return text
    names = split_artists(artist)
    if len(names) <= 1:
        return text
    guests = join_names_natural(names[1:])
    if not guests:
        return text
    if ' feat. ' in text.casefold():
        return text
    if not text:
        return f'feat. {guests}'
    return f'{text} feat. {guests}'


def primary_artist(value: str | None) -> str:
    """First name from a multi-artist string (empty if none)."""
    names = split_artists(value)
    return names[0] if names else ''
