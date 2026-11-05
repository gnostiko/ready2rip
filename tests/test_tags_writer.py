# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for tag-field helpers (no audio files required)."""

from __future__ import annotations

from ready2rip.metadata.providers import AlbumMetadata, TrackMetadata
from ready2rip.tags.writer import (
    TagWriter,
    _album_barcode,
    _album_date,
    _artist_names_for_tags,
    _is_compilation,
    _track_isrc,
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


def test_apply_vorbis_writes_date_barcode_isrc() -> None:
    audio: dict[str, list[str]] = {}
    album = AlbumMetadata(
        title='Album',
        artist='A; B',
        date='1997-06-16',
        genre='Rock',
        label='Label',
        barcode='0123456789012',
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
                isrc='USRC17607839',
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
    assert audio['DATE'] == ['1997-06-16']
    assert audio['BARCODE'] == ['0123456789012']
    assert audio['ISRC'] == ['USRC17607839']
    # Still omitted extras.
    assert 'MEDIA' not in audio
    assert 'MUSICBRAINZ_RECORDINGID' not in audio
    assert 'MUSICBRAINZ_ALBUMID' not in audio
    assert 'CATALOGNUMBER' not in audio
    assert 'RELEASECOUNTRY' not in audio
    assert 'MUSICBRAINZ_DISCID' not in audio


def test_date_barcode_isrc_helpers() -> None:
    album = AlbumMetadata(
        date='1997',
        barcode='0123456789012',
        tracks=[TrackMetadata(number=1, isrc='usrc17607839')],
    )
    assert _album_date(album) == '1997'
    assert _album_barcode(album) == '0123456789012'
    assert _track_isrc(album, 1) == 'USRC17607839'
    assert _album_barcode(AlbumMetadata(barcode='123')) == ''
    assert _track_isrc(AlbumMetadata(tracks=[]), 1) == ''


def test_apply_vorbis_image_mode_omits_track_and_isrc() -> None:
    audio: dict[str, list[str]] = {
        'TRACKNUMBER': ['1'],
        'ISRC': ['USRC17607839'],
    }
    album = AlbumMetadata(
        title='Whole Album',
        artist='Band',
        date='2001',
        barcode='0123456789012',
        tracks=[
            TrackMetadata(
                number=1, title='Song One', artist='Band', isrc='USRC17607839'
            )
        ],
    )
    TagWriter()._apply_vorbis(
        audio,
        title='Whole Album',
        artists=['Band'],
        album='Whole Album',
        album_artists=['Band'],
        track_number=0,
        total_tracks=None,
        album_meta=album,
    )
    assert audio['TITLE'] == ['Whole Album']
    assert audio['DATE'] == ['2001']
    assert audio['BARCODE'] == ['0123456789012']
    assert 'TRACKNUMBER' not in audio
    assert 'ISRC' not in audio
