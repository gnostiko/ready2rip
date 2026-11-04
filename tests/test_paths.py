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
    scheme_tooltip,
    template_values,
)
from ready2rip.settings import (
    _migrate_album_folder_template,
    _migrate_filename_template,
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
    assert '{disc_folder}' not in DEFAULT_ALBUM_FOLDER_TEMPLATE


def test_scheme_index_matches_and_custom() -> None:
    assert scheme_index(FOLDER_SCHEMES, DEFAULT_ALBUM_FOLDER_TEMPLATE) == 0
    assert scheme_index(FILENAME_SCHEMES, DEFAULT_FILENAME_TEMPLATE) == 0
    assert scheme_index(FOLDER_SCHEMES, '{album_artist}/{album} ({year})') == 1
    assert scheme_index(FOLDER_SCHEMES, '{album_artist}/{album}') == 2
    assert scheme_index(FOLDER_SCHEMES, '{album_artist}/Weird') == len(FOLDER_SCHEMES) - 1
    assert len(FOLDER_SCHEMES) == 4
    assert len(FILENAME_SCHEMES) == 3


def test_scheme_tooltip_includes_template_and_description() -> None:
    tip = scheme_tooltip(FOLDER_SCHEMES[0])
    assert FOLDER_SCHEMES[0].template in tip
    assert 'release date' in tip.lower() or 'date' in tip.lower()
    custom = scheme_tooltip(FILENAME_SCHEMES[-1])
    assert 'Custom' in custom
    assert 'template' in custom.lower()


def test_migrate_strips_disc_folder() -> None:
    assert (
        _migrate_album_folder_template('{album_artist}/{dated_album}/{disc_folder}')
        == DEFAULT_ALBUM_FOLDER_TEMPLATE
    )
    assert (
        _migrate_album_folder_template('{album_artist}/{album}/{disc_folder}')
        == '{album_artist}/{album}'
    )
    assert (
        _migrate_album_folder_template('{genre}/{album_artist}/{album}/{disc_folder}')
        == '{genre}/{album_artist}/{album}'
    )


def test_migrate_filename_default_to_disc_track() -> None:
    assert _migrate_filename_template('{track:02d} - {title}') == DEFAULT_FILENAME_TEMPLATE
    assert _migrate_filename_template('') == DEFAULT_FILENAME_TEMPLATE
    assert (
        _migrate_filename_template('{disc_track} - {artist} - {title}')
        == '{disc_track} - {artist} - {title}'
    )


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
    assert values['disc_folder'] == 'CD1'  # Custom-only token
    assert values['disc_track'] == '1-02'
    assert values['disc_prefix'] == '1-'
    assert values['genre'] == 'Alternative Rock'
    assert values['albumartist'] == values['album_artist']

    album_dir, path = build_output_paths(
        base_dir=Path('/tmp/music'),
        album_folder_template='{genre}/{album_artist}/{album}',
        filename_template='{disc_track} - {artist} - {title}',
        album=album,
        track_number=2,
        extension='flac',
    )
    assert album_dir == Path('/tmp/music/Alternative Rock/Radiohead/OK Computer')
    assert path.name == '1-02 - Radiohead - Paranoid Android.flac'


def test_empty_genre_token_has_fallback() -> None:
    album = _album()
    album.genre = ''
    values = template_values(album=album, track_number=1)
    assert values['genre'] == 'Unknown Genre'


def test_disc_track_only_prefixes_multi_disc() -> None:
    album = _album()
    album.medium_count = 1
    single = template_values(album=album, track_number=1)
    assert single['disc_folder'] == ''
    assert single['disc_track'] == '01'
    assert single['disc_prefix'] == ''

    album.medium_count = 3
    album.medium_position = 2
    multi = template_values(album=album, track_number=4)
    assert multi['disc_folder'] == 'CD2'
    assert multi['disc_track'] == '2-04'
    assert multi['disc_prefix'] == '2-'


def test_artist_style_feat_and_slash_in_paths() -> None:
    album = AlbumMetadata(
        title='Collab',
        artist='Main; Guest; Other',
        date='2020-01-01',
        medium_position=1,
        medium_count=1,
        tracks=[
            TrackMetadata(
                number=1,
                title='Song',
                artist='Main; Guest; Other',
            ),
        ],
    )
    feat = template_values(album=album, track_number=1, artist_style='feat')
    assert feat['album_artist'] == 'Main'
    assert feat['artist'] == 'Main'
    assert feat['title'] == 'Song feat. Guest & Other'

    slash = template_values(album=album, track_number=1, artist_style='slash')
    # ASCII ``/`` cannot appear in path components; use fullwidth solidus.
    assert slash['artist'] == 'Main ／ Guest ／ Other'
    assert slash['title'] == 'Song'

    album_dir, path = build_output_paths(
        base_dir=Path('/tmp/music'),
        album_folder_template='{album_artist}/{album}',
        filename_template='{track:02d} - {title}',
        album=album,
        track_number=1,
        extension='.flac',
        artist_style='feat',
    )
    assert album_dir == Path('/tmp/music/Main/Collab')
    assert path.name == '01 - Song feat. Guest & Other.flac'
