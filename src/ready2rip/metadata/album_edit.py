# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure album-edit helpers (no GTK)."""

from __future__ import annotations

from dataclasses import replace

from ready2rip.metadata.providers import AlbumMetadata, TrackMetadata


def mark_album_edited(album: AlbumMetadata) -> None:
    """Annotate metadata source after a manual edit."""
    source = album.source or ''
    if source in ('musicbrainz', 'gnudb', 'freedb') and not source.endswith('+manual'):
        album.source = f'{source}+manual'
    elif not source:
        album.source = 'manual'


def gap_fill_album(target: AlbumMetadata, donor: AlbumMetadata) -> AlbumMetadata:
    """Copy empty title/artist/genre/barcode/ISRC fields from *donor* into *target*."""
    changed = False
    title = target.title or donor.title
    artist = target.artist or donor.artist
    genre = target.genre or donor.genre
    barcode = target.barcode or donor.barcode
    if (
        title != target.title
        or artist != target.artist
        or genre != target.genre
        or barcode != target.barcode
    ):
        changed = True

    donor_tracks = {t.number: t for t in donor.tracks}
    new_tracks: list[TrackMetadata] = []
    seen: set[int] = set()
    for track in target.tracks:
        seen.add(track.number)
        other = donor_tracks.get(track.number)
        if other is None:
            new_tracks.append(track)
            continue
        merged = replace(
            track,
            title=track.title or other.title,
            artist=track.artist or other.artist,
            isrc=track.isrc or other.isrc,
        )
        if merged != track:
            changed = True
        new_tracks.append(merged)
    for num, other in sorted(donor_tracks.items()):
        if num in seen:
            continue
        new_tracks.append(other)
        changed = True

    if not changed:
        return target
    return replace(
        target,
        title=title,
        artist=artist,
        genre=genre,
        barcode=barcode,
        tracks=new_tracks,
    )
