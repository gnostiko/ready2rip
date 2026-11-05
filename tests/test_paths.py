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
    migrate_album_folder_template,
    migrate_filename_template,
    scheme_index,
    scheme_tooltip,
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
    assert DEFAULT_ALBUM_FOLDER_TEMPLATE.endswith('{disc_folder}')
    assert 'Multi-disc' not in FILENAME_SCHEMES[0].description
    assert 'Multi-disc' not in FILENAME_SCHEMES[1].description
    assert '1-01' not in FILENAME_SCHEMES[0].description
    assert '1-01' not in FILENAME_SCHEMES[1].description


def test_scheme_index_matches_and_custom() -> None:
    assert scheme_index(FOLDER_SCHEMES, DEFAULT_ALBUM_FOLDER_TEMPLATE) == 0
    assert scheme_index(FILENAME_SCHEMES, DEFAULT_FILENAME_TEMPLATE) == 0
    assert (
        scheme_index(FOLDER_SCHEMES, '{album_artist}/{album} ({year})/{disc_folder}')
        == 1
    )
    assert scheme_index(FOLDER_SCHEMES, '{album_artist}/{album}/{disc_folder}') == 2
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


def test_migrate_adds_disc_folder_to_presets() -> None:
    assert (
        migrate_album_folder_template('{album_artist}/{dated_album}')
        == DEFAULT_ALBUM_FOLDER_TEMPLATE
    )
    assert (
        migrate_album_folder_template('{album_artist}/{dated_album}/{disc_folder}')
        == DEFAULT_ALBUM_FOLDER_TEMPLATE
    )
    assert (
        migrate_album_folder_template('{album_artist}/{album}')
        == '{album_artist}/{album}/{disc_folder}'
    )
    assert (
        migrate_album_folder_template('{genre}/{album_artist}/{album}/{disc_folder}')
        == '{genre}/{album_artist}/{album}/{disc_folder}'
    )


def test_migrate_filename_drops_disc_prefix_templates() -> None:
    assert migrate_filename_template('{disc_track} - {title}') == DEFAULT_FILENAME_TEMPLATE
    assert migrate_filename_template('{track:02d} - {title}') == DEFAULT_FILENAME_TEMPLATE
    assert migrate_filename_template('') == DEFAULT_FILENAME_TEMPLATE
    assert (
        migrate_filename_template('{disc_track} - {artist} - {title}')
        == '{track:02d} - {artist} - {title}'
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
    # Single-disc: no CD# folder.
    assert album_dir == Path('/tmp/music/Radiohead/(1997-06-16) - OK Computer')
    assert path == album_dir / '01 - Airbag.flac'


def test_build_output_paths_multi_disc_uses_cd_folder() -> None:
    album = _album()
    album.medium_count = 2
    album.medium_position = 1
    values = template_values(album=album, track_number=2)
    assert values['disc_folder'] == 'CD1'
    assert values['disc_track'] == '02'
    assert values['disc_prefix'] == ''

    album_dir, path = build_output_paths(
        base_dir=Path('/tmp/music'),
        album_folder_template=DEFAULT_ALBUM_FOLDER_TEMPLATE,
        filename_template=DEFAULT_FILENAME_TEMPLATE,
        album=album,
        track_number=2,
        extension='flac',
    )
    assert album_dir == Path(
        '/tmp/music/Radiohead/(1997-06-16) - OK Computer/CD1'
    )
    assert path.name == '02 - Paranoid Android.flac'


def test_empty_genre_token_has_fallback() -> None:
    album = _album()
    album.genre = ''
    values = template_values(album=album, track_number=1)
    assert values['genre'] == 'Unknown Genre'


def test_disc_track_never_prefixes_disc_number() -> None:
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
    assert multi['disc_track'] == '04'
    assert multi['disc_prefix'] == ''


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
        album_folder_template='{album_artist}/{album}/{disc_folder}',
        filename_template='{track:02d} - {title}',
        album=album,
        track_number=1,
        extension='.flac',
        artist_style='feat',
    )
    assert album_dir == Path('/tmp/music/Main/Collab')
    assert path.name == '01 - Song feat. Guest & Other.flac'
