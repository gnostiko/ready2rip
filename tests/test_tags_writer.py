# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for tag-field helpers (no audio files required)."""

from __future__ import annotations

from ready2rip.metadata.providers import AlbumMetadata, TrackMetadata
from ready2rip.tags.writer import (
    TagWriter,
    _artist_names_for_tags,
    _is_compilation,
)


def test_artist_names_multi_value_and_feat() -> None:
    value = 'Main; Guest; Other'
    assert _artist_names_for_tags(value, style='semicolon') == [
        'Main',
        'Guest',
        'Other',
    ]
    assert _artist_names_for_tags(value, style='slash') == [
        'Main',
        'Guest',
        'Other',
    ]
    assert _artist_names_for_tags(value, style='feat') == ['Main']


def test_is_compilation_various_artists() -> None:
    assert _is_compilation(AlbumMetadata(artist='Various Artists'))
    assert _is_compilation(AlbumMetadata(artist='VA'))
    assert not _is_compilation(AlbumMetadata(artist='Radiohead'))


def test_apply_vorbis_keeps_core_fields_only() -> None:
    audio: dict[str, list[str]] = {}
    album = AlbumMetadata(
        title='Album',
        artist='A; B',
        date='1997-06-16',
        genre='Rock',
        label='Label',
        barcode='123',
        catalog_number='CAT',
        country='GB',
        discid='disc',
        musicbrainz_release_id='rel-id',
        musicbrainz_release_group_id='rg-id',
        medium_position=2,
        medium_count=3,
        tracks=[
            TrackMetadata(
                number=1,
                title='Song',
                artist='A; B',
                musicbrainz_recording_id='rec-id',
            )
        ],
    )
    TagWriter()._apply_vorbis(
        audio,
        title='Song',
        artists=['A', 'B'],
        album='Album',
        album_artists=['A', 'B'],
        track_number=1,
        total_tracks=10,
        album_meta=album,
    )
    assert audio['TITLE'] == ['Song']
    assert audio['ARTIST'] == ['A', 'B']
    assert audio['ALBUM'] == ['Album']
    assert audio['ALBUMARTIST'] == ['A', 'B']
    assert audio['TRACKNUMBER'] == ['1']
    assert audio['TRACKTOTAL'] == ['10']
    assert audio['DISCNUMBER'] == ['2']
    assert audio['DISCTOTAL'] == ['3']
    assert audio['GENRE'] == ['Rock']
    assert audio['LABEL'] == ['Label']
    # Explicitly omitted extras.
    assert 'DATE' not in audio
    assert 'ORIGINALDATE' not in audio
    assert 'MEDIA' not in audio
    assert 'MUSICBRAINZ_RECORDINGID' not in audio
    assert 'MUSICBRAINZ_ALBUMID' not in audio
    assert 'BARCODE' not in audio
    assert 'CATALOGNUMBER' not in audio
    assert 'RELEASECOUNTRY' not in audio
    assert 'MUSICBRAINZ_DISCID' not in audio
