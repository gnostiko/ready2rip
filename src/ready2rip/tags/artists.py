# SPDX-License-Identifier: GPL-3.0-or-later
"""Multi-artist helpers (dBpoweramp-style semicolon separation).

In the UI and string form, multiple artists are written as::

    Artist One; Artist Two; Artist Three

When tagging:
  * Vorbis/FLAC/Opus — one ARTIST / ALBUMARTIST field per name
  * ID3 (MP3/WAV) — multi-value TPE1 / TPE2 text frames
"""

from __future__ import annotations

# dBpoweramp multi-value display / entry separator (semicolon + space).
ARTIST_SEPARATOR = '; '


def split_artists(value: str | None) -> list[str]:
    """Split a multi-artist string on ``;`` into non-empty names.

    Accepts ``A; B``, ``A;B``, and trims whitespace. Empty segments are dropped.
    """
    if not value:
        return []
    parts = [p.strip() for p in str(value).split(';')]
    return [p for p in parts if p]


def join_artists(artists: list[str] | tuple[str, ...] | str | None) -> str:
    """Join artist names with ``'; `` for display / storage in metadata strings."""
    if artists is None:
        return ''
    if isinstance(artists, str):
        return join_artists(split_artists(artists))
    cleaned = [a.strip() for a in artists if a and str(a).strip()]
    return ARTIST_SEPARATOR.join(cleaned)


def normalize_artists(value: str | None) -> str:
    """Canonical multi-artist form: ``Name; Name`` (no empty segments)."""
    return join_artists(split_artists(value))


def first_artist(value: str | None, *, fallback: str = 'Unknown Artist') -> str:
    """First name only (useful for short path components)."""
    names = split_artists(value)
    return names[0] if names else fallback
