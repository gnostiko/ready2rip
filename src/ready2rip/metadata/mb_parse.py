# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure MusicBrainz JSON helpers (no HTTP)."""

from __future__ import annotations

import re
from typing import Any


def _mb_best_named_count(
    entries: Any,
    *,
    skip_years: bool = False,
) -> str:
    """Highest-count ``name`` from a MusicBrainz genres/tags list."""
    if not isinstance(entries, list):
        return ''
    best_name = ''
    best_count = -1
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get('name') or '').strip()
        if not name:
            continue
        if skip_years and re.fullmatch(r'\d{4}', name):
            continue
        try:
            count = int(entry.get('count') or 0)
        except (TypeError, ValueError):
            count = 0
        # Highest vote wins; alphabetical tie-break for stability.
        if count > best_count or (count == best_count and name < best_name):
            best_count = count
            best_name = name
    return best_name


def _mb_primary_genre(entity: dict[str, Any] | None) -> str:
    """Return the single highest-voted MusicBrainz genre name, or empty.

    Prefers official ``genres``; falls back to ``tags`` when genres are absent
    (skipping year-like tags). MusicBrainz stores names lowercase; we
    title-case for tags/UI (e.g. ``indie rock`` → ``Indie Rock``).
    """
    if not entity:
        return ''
    best = _mb_best_named_count(entity.get('genres') or [])
    if not best:
        best = _mb_best_named_count(
            entity.get('tags') or [],
            skip_years=True,
        )
    return best.title() if best else ''