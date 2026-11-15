# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for metadata picker subtitle / disc labels."""

from __future__ import annotations

from ready2rip.metadata.providers import AlbumMetadata, TrackMetadata
from ready2rip.metadata_dialog import MetadataPickerDialog


def test_disc_label_only_for_multi_disc() -> None:
    single = AlbumMetadata(medium_position=1, medium_count=1)
    assert MetadataPickerDialog._disc_label(single) is None
    multi = AlbumMetadata(medium_position=2, medium_count=3)
    assert MetadataPickerDialog._disc_label(multi) == 'Disc 2/3'
    odd = AlbumMetadata(medium_position=2, medium_count=1)
    assert MetadataPickerDialog._disc_label(odd) == 'Disc 2/1'


def test_subtitle_includes_disc_and_track_mismatch() -> None:
    album = AlbumMetadata(
        title='Album',
        artist='Artist',
        date='2003-10-14',
        country='US',
        medium_position=1,
        medium_count=2,
        tracks=[TrackMetadata(number=i, title=f'T{i}') for i in range(1, 13)],
    )
    text = MetadataPickerDialog._subtitle(album, disc_track_count=14)
    assert 'Artist' in text
    assert '2003' in text
    assert 'Disc 1/2' in text
    assert '12 tracks (disc has 14)' in text
    assert 'US' in text


def test_track_count_mismatch_badge() -> None:
    album = AlbumMetadata(
        tracks=[TrackMetadata(number=1, title='A'), TrackMetadata(number=2, title='B')]
    )
    assert MetadataPickerDialog._track_count_mismatch(album, 2) is None
    assert MetadataPickerDialog._track_count_mismatch(album, 14) == '2 ≠ 14'
    assert MetadataPickerDialog._track_count_mismatch(album, 0) is None
