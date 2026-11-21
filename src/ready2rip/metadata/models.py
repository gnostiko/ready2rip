# SPDX-License-Identifier: GPL-3.0-or-later
"""Metadata datamodels (no network I/O)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TrackMetadata:
    number: int
    title: str = ''
    artist: str = ''
    musicbrainz_recording_id: str = ''
    isrc: str = ''
    # Unsynced lyrics (plain text) and optional LRC; filled by LRCLIB / manual.
    lyrics: str = ''
    lyrics_synced: str = ''
    lyrics_source: str = ''


@dataclass
class AlbumMetadata:
    """Normalized album/release metadata used by the tagger."""

    title: str = ''
    artist: str = ''
    date: str = ''
    genre: str = ''
    barcode: str = ''
    label: str = ''
    catalog_number: str = ''
    musicbrainz_release_id: str = ''
    musicbrainz_release_group_id: str = ''
    discid: str = ''
    tracks: list[TrackMetadata] = field(default_factory=list)
    cover_url: str = ''
    source: str = ''  # musicbrainz | gnudb | freedb | manual
    country: str = ''
    status: str = ''
    disambiguation: str = ''
    medium_count: int = 1
    medium_position: int = 1

    @property
    def display_label(self) -> str:
        bits = [self.artist or 'Unknown Artist', self.title or 'Unknown Album']
        extra = []
        if self.date:
            extra.append(self.date[:4] if len(self.date) >= 4 else self.date)
        if self.genre:
            extra.append(self.genre)
        if self.country:
            extra.append(self.country)
        if self.disambiguation:
            extra.append(self.disambiguation)
        if self.status and self.status.lower() != 'official':
            extra.append(self.status)
        if extra:
            return f'{" – ".join(bits)} ({", ".join(extra)})'
        return ' – '.join(bits)