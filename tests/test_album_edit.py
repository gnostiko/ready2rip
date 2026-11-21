# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for pure album-edit helpers."""

from __future__ import annotations

from ready2rip.metadata.album_edit import gap_fill_album, mark_album_edited
from ready2rip.metadata.models import AlbumMetadata, TrackMetadata


def test_mark_album_edited_annotates_source() -> None:
    album = AlbumMetadata(source='musicbrainz', title='A')
    mark_album_edited(album)
    assert album.source == 'musicbrainz+manual'
    mark_album_edited(album)
    assert album.source == 'musicbrainz+manual'


def test_gap_fill_album_fills_empty_fields() -> None:
    target = AlbumMetadata(
        title='',
        artist='Keep',
        tracks=[TrackMetadata(number=1, title='', artist='', isrc='')],
    )
    donor = AlbumMetadata(
        title='Filled',
        artist='Other',
        barcode='123',
        tracks=[TrackMetadata(number=1, title='Song', artist='Feat', isrc='USABC')],
    )
    merged = gap_fill_album(target, donor)
    assert merged.title == 'Filled'
    assert merged.artist == 'Keep'
    assert merged.barcode == '123'
    assert merged.tracks[0].title == 'Song'
    assert merged.tracks[0].isrc == 'USABC'
