# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for output path templates and naming schemes."""

from __future__ import annotations

from pathlib import Path

from ready2rip.metadata.providers import AlbumMetadata, TrackMetadata
from ready2rip.paths import (
    DEFAULT_ALBUM_FOLDER_TEMPLATE,
    DEFAULT_FILENAME_TEMPLATE,
    FILENAME_SCHEMES,
    FOLDER_SCHEMES,
    build_output_paths,
    scheme_index,
    template_values,
)


def _album() -> AlbumMetadata:
    return AlbumMetadata(
        title='OK Computer',
        artist='Radiohead',
        date='1997-06-16',
        genre='Alternative Rock',
        label='Parlophone',
        medium_position=1,
        medium_count=1,
        tracks=[
            TrackMetadata(number=1, title='Airbag', artist='Radiohead'),
            TrackMetadata(number=2, title='Paranoid Android', artist='Radiohead'),
        ],
    )


def test_default_schemes_are_first() -> None:
    assert FOLDER_SCHEMES[0].template == DEFAULT_ALBUM_FOLDER_TEMPLATE
    assert FILENAME_SCHEMES[0].template == DEFAULT_FILENAME_TEMPLATE
    assert FOLDER_SCHEMES[-1].id == 'custom'
    assert FILENAME_SCHEMES[-1].id == 'custom'


def test_scheme_index_matches_and_custom() -> None:
    assert scheme_index(FOLDER_SCHEMES, DEFAULT_ALBUM_FOLDER_TEMPLATE) == 0
    assert scheme_index(FILENAME_SCHEMES, DEFAULT_FILENAME_TEMPLATE) == 0
    assert scheme_index(FOLDER_SCHEMES, '{album_artist}/{album}/{disc_folder}') == 1
    assert scheme_index(FOLDER_SCHEMES, '{album_artist}/Weird') == len(FOLDER_SCHEMES) - 1


def test_build_output_paths_default() -> None:
    album_dir, path = build_output_paths(
        base_dir=Path('/tmp/music'),
        album_folder_template=DEFAULT_ALBUM_FOLDER_TEMPLATE,
        filename_template=DEFAULT_FILENAME_TEMPLATE,
        album=_album(),
        track_number=1,
        extension='.flac',
    )
    assert album_dir == Path('/tmp/music/Radiohead/(1997-06-16) - OK Computer')
    assert path == album_dir / '01 - Airbag.flac'


def test_build_output_paths_va_and_genre_schemes() -> None:
    album = _album()
    album.medium_count = 2
    values = template_values(album=album, track_number=2)
    assert values['disc_folder'] == 'CD1'
    assert values['genre'] == 'Alternative Rock'
    assert values['albumartist'] == values['album_artist']

    album_dir, path = build_output_paths(
        base_dir=Path('/tmp/music'),
        album_folder_template='{genre}/{album_artist}/{album}/{disc_folder}',
        filename_template='{track:02d} - {artist} - {title}',
        album=album,
        track_number=2,
        extension='flac',
    )
    assert album_dir == Path(
        '/tmp/music/Alternative Rock/Radiohead/OK Computer/CD1'
    )
    assert path.name == '02 - Radiohead - Paranoid Android.flac'


def test_empty_genre_token_has_fallback() -> None:
    album = _album()
    album.genre = ''
    values = template_values(album=album, track_number=1)
    assert values['genre'] == 'Unknown Genre'
