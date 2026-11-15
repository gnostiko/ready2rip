# SPDX-License-Identifier: GPL-3.0-or-later
"""Plain-text helpers for the metadata release picker (no GTK)."""

from __future__ import annotations

from ready2rip.metadata.providers import AlbumMetadata


def disc_label(album: AlbumMetadata) -> str | None:
    """Return ``Disc N/M`` when the release is multi-disc or not disc 1."""
    try:
        position = max(1, int(album.medium_position or 1))
    except (TypeError, ValueError):
        position = 1
    try:
        count = max(1, int(album.medium_count or 1))
    except (TypeError, ValueError):
        count = 1
    if count > 1 or position > 1:
        return f'Disc {position}/{count}'
    return None


def track_count_mismatch(album: AlbumMetadata, disc_track_count: int) -> str | None:
    """Short badge when medium track count ≠ inserted disc, else ``None``."""
    if disc_track_count <= 0 or not album.tracks:
        return None
    n = len(album.tracks)
    if n == disc_track_count:
        return None
    return f'{n} ≠ {disc_track_count}'


def subtitle(album: AlbumMetadata, *, disc_track_count: int = 0) -> str:
    """One-line subtitle for a picker row (artist, year, disc, tracks, …)."""
    bits = [album.artist or 'Unknown Artist']
    if album.date:
        bits.append(album.date[:4] if len(album.date) >= 4 else album.date)
    disc = disc_label(album)
    if disc:
        bits.append(disc)
    if album.tracks:
        n = len(album.tracks)
        if disc_track_count > 0 and n != disc_track_count:
            bits.append(f'{n} tracks (disc has {disc_track_count})')
        else:
            bits.append(f'{n} tracks')
    if album.status and album.status.lower() not in {'', 'official'}:
        bits.append(album.status)
    if album.country:
        bits.append(album.country)
    if album.disambiguation:
        bits.append(album.disambiguation)
    if album.label:
        bits.append(album.label)
    if album.catalog_number:
        bits.append(album.catalog_number)
    if album.genre:
        bits.append(album.genre)
    return ' · '.join(bits)
