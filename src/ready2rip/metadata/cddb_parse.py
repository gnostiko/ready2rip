# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure CDDB/FreeDB text parsing (no HTTP)."""

from __future__ import annotations

import re

from ready2rip.metadata.models import AlbumMetadata, TrackMetadata
from ready2rip.util import normalize_release_date

# FreeDB has no native disc-number field; multi-disc sets encode it in DTITLE/EXTD.
# Escape \# — (?x) treats bare # as a comment to end-of-line.
_DISC_MARK_RE = re.compile(
    r'(?i)'
    r'[\(\[{]?\s*'
    r'(?:disc|disk|cd|dvd)\s*(?:#\s*)?'
    r'(?P<pos>\d{1,2})'
    r'(?:\s*(?:of|/)\s*(?P<total>\d{1,2}))?'
    r'\s*[)\]}]?'
    r'|'
    r'[\(\[]\s*(?P<pos2>\d{1,2})\s*/\s*(?P<total2>\d{1,2})\s*[)\]]'
)


def _disc_fields_from_cddb_text(
    title: str, extd: str = ''
) -> tuple[int, int, str]:
    """Extract disc N[/M] from a FreeDB title or EXTD blob.

    Returns ``(medium_position, medium_count, cleaned_title)``. Defaults to
    ``(1, 1, title)`` when no disc marker is found.
    """
    position = 1
    total = 1
    cleaned = (title or '').strip()

    def _apply(match: re.Match[str], *, strip_from_title: bool) -> None:
        nonlocal position, total, cleaned
        pos_raw = match.group('pos') or match.group('pos2')
        tot_raw = match.group('total') or match.group('total2')
        if not pos_raw:
            return
        pos = int(pos_raw)
        tot = int(tot_raw) if tot_raw else max(pos, 1)
        if pos < 1:
            return
        position = pos
        total = max(tot, pos)
        if strip_from_title:
            cleaned = (cleaned[: match.start()] + cleaned[match.end() :]).strip()
            cleaned = re.sub(r'[\s\-–—:/]+$', '', cleaned).strip()

    title_match = _DISC_MARK_RE.search(cleaned)
    if title_match is not None:
        _apply(title_match, strip_from_title=True)
    elif extd:
        extd_match = _DISC_MARK_RE.search(extd)
        if extd_match is not None:
            _apply(extd_match, strip_from_title=False)

    return position, total, cleaned or (title or '').strip()


def _parse_cddb_query_matches(
    text: str,
    *,
    fallback_id: str,
) -> list[tuple[str, str]]:
    """Parse ``cddb query`` status lines into ``(category, discid)`` pairs."""
    matches: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or line == '.':
            continue
        parts = line.split()
        if parts and parts[0].isdigit():
            parts = parts[1:]
        if len(parts) < 2:
            continue
        category, disc_id = parts[0].lower(), parts[1].lower()
        if not re.fullmatch(r'[a-z]+', category):
            continue
        if not re.fullmatch(r'[0-9a-f]{8}', disc_id):
            disc_id = fallback_id.lower()
            if not re.fullmatch(r'[0-9a-f]{8}', disc_id):
                continue
        key = (category, disc_id)
        if key in seen:
            continue
        seen.add(key)
        matches.append(key)
    return matches


def _parse_cddb_entry(
    text: str,
    source: str = 'gnudb',
    *,
    category: str = '',
) -> AlbumMetadata | None:
    """Parse a classic CDDB/FreeDB entry body into AlbumMetadata."""
    if not text or 'DTITLE=' not in text:
        return None

    dtitle = ''
    dyear = ''
    dgenre = ''
    extd = ''
    tracks: dict[int, str] = {}

    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith('#'):
            continue
        if line.startswith('DTITLE='):
            dtitle += line[7:]
        elif line.startswith('DYEAR='):
            dyear = normalize_release_date(line[6:].strip())
        elif line.startswith('DGENRE='):
            dgenre = line[7:].strip()
        elif line.startswith('EXTD='):
            extd += line[5:]
        else:
            match = re.match(r'TTITLE(\d+)=(.*)', line)
            if match:
                idx = int(match.group(1))
                tracks[idx] = tracks.get(idx, '') + match.group(2)

    artist = ''
    title = dtitle
    if ' / ' in dtitle:
        artist, title = dtitle.split(' / ', 1)

    title = title.strip()
    medium_position, medium_count, title = _disc_fields_from_cddb_text(
        title, extd
    )

    track_list = [
        TrackMetadata(number=i + 1, title=tracks[i], artist=artist)
        for i in sorted(tracks)
    ]
    if not title and not track_list:
        return None

    # Prefer DGENRE; fall back to the CDDB category (rock, jazz, …).
    raw_genre = (dgenre or category or '').strip()
    genre = raw_genre.title() if raw_genre else ''

    return AlbumMetadata(
        title=title.strip(),
        artist=artist.strip(),
        date=dyear,
        genre=genre,
        tracks=track_list,
        source=source,
        medium_position=medium_position,
        medium_count=medium_count,
    )