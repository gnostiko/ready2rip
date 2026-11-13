# SPDX-License-Identifier: GPL-3.0-or-later
"""Also-encode destination helpers (no PyGObject)."""

from __future__ import annotations

from pathlib import Path

from ready2rip.rip.encode_dest import (
    audio_dir_for_format,
    encode_format_list,
    format_folder_name,
    is_lossy_format,
    normalize_encode_format,
    should_embed_artwork_for_format,
)


def test_encode_format_list_primary_only_by_default() -> None:
    assert encode_format_list('flac') == ['flac']
    assert encode_format_list('flac', also_encode=False, also_format='mp3') == [
        'flac'
    ]


def test_encode_format_list_adds_lossy_companion() -> None:
    assert encode_format_list(
        'flac', also_encode=True, also_format='mp3'
    ) == ['flac', 'mp3']
    assert encode_format_list(
        'flac', also_encode=True, also_format='opus'
    ) == ['flac', 'opus']
    assert encode_format_list(
        'flac', also_encode=True, also_format='qaac'
    ) == ['flac', 'qaac']
    assert encode_format_list(
        'flac', also_encode=True, also_format='aac'
    ) == ['flac', 'aac']


def test_encode_format_list_forces_lossless_primary() -> None:
    # Legacy lossy primary migrates to FLAC; also-encode still adds lossy.
    assert encode_format_list(
        'mp3', also_encode=True, also_format='mp3'
    ) == ['flac', 'mp3']
    assert encode_format_list(
        'flac', also_encode=True, also_format='mp3', allow_also=False
    ) == ['flac']


def test_normalize_encode_format() -> None:
    assert normalize_encode_format('flac') == 'flac'
    assert normalize_encode_format('WAV') == 'wav'
    assert normalize_encode_format('mp3') == 'flac'
    assert normalize_encode_format('opus') == 'flac'


def test_audio_dir_format_subdir_only_when_multi() -> None:
    album = Path('/music/Artist/Album')
    assert audio_dir_for_format(
        album, 'flac', folder_layout='format_subdir', multi_format=True
    ) == album / 'FLAC'
    assert audio_dir_for_format(
        album, 'mp3', folder_layout='format_subdir', multi_format=True
    ) == album / 'MP3'
    assert audio_dir_for_format(
        album, 'flac', folder_layout='format_subdir', multi_format=False
    ) == album
    assert audio_dir_for_format(
        album, 'mp3', folder_layout='same_folder', multi_format=True
    ) == album
    # Custom folder is a separate output root — no format subfolder here.
    assert audio_dir_for_format(
        album, 'mp3', folder_layout='custom_folder', multi_format=True
    ) == album


def test_format_folder_name() -> None:
    assert format_folder_name('aac') == 'M4A'
    assert format_folder_name('qaac') == 'M4A'
    assert format_folder_name('opus') == 'Opus'
    assert format_folder_name('aiff') == 'AIFF'


def test_is_lossy_format() -> None:
    assert is_lossy_format('mp3')
    assert is_lossy_format('AAC')
    assert is_lossy_format('qaac')
    assert is_lossy_format('opus')
    assert not is_lossy_format('flac')
    assert not is_lossy_format('wav')
    assert not is_lossy_format('aiff')


def test_should_embed_artwork_for_format() -> None:
    assert should_embed_artwork_for_format(
        'mp3', embed_lossy=True, embed_lossless=False
    )
    assert should_embed_artwork_for_format(
        'aac', embed_lossy=True, embed_lossless=False
    )
    assert should_embed_artwork_for_format(
        'qaac', embed_lossy=True, embed_lossless=False
    )
    assert not should_embed_artwork_for_format(
        'flac', embed_lossy=True, embed_lossless=False
    )
    assert should_embed_artwork_for_format(
        'flac', embed_lossy=True, embed_lossless=True
    )
    assert should_embed_artwork_for_format(
        'wav', embed_lossy=False, embed_lossless=True
    )
    assert not should_embed_artwork_for_format(
        'opus', embed_lossy=False, embed_lossless=True
    )


def test_unique_album_dirs_skips_lossy_for_data_bins(tmp_path: Path) -> None:
    from ready2rip.rip.engine import _unique_album_dirs

    meta = tmp_path / 'Album'
    flac_dir = meta / 'FLAC'
    mp3_dir = meta / 'MP3'
    flac_dir.mkdir(parents=True)
    mp3_dir.mkdir(parents=True)
    outputs = [
        flac_dir / '01.flac',
        mp3_dir / '01.mp3',
        meta / 'cover.jpg',
    ]
    dirs = _unique_album_dirs(outputs, meta)
    assert dirs[0] == meta
    assert flac_dir in dirs
    assert mp3_dir not in dirs


def test_existing_files_policy_skip_and_version(tmp_path: Path) -> None:
    from ready2rip.rip.engine import RipEngine, _EncodeDest

    existing = tmp_path / '01 - Title.flac'
    existing.write_bytes(b'x')
    fresh = tmp_path / '02 - Other.flac'
    dests = [
        _EncodeDest(
            format='flac',
            base_dir=tmp_path,
            album_dir=tmp_path,
            out_path=existing,
            meta_dir=tmp_path,
            primary=True,
        ),
        _EncodeDest(
            format='flac',
            base_dir=tmp_path,
            album_dir=tmp_path,
            out_path=fresh,
            meta_dir=tmp_path,
            primary=True,
        ),
    ]
    skipped = RipEngine._apply_existing_files_policy(dests, 'skip')
    assert [d.out_path for d in skipped] == [fresh]

    versioned = RipEngine._apply_existing_files_policy(dests, 'version')
    assert versioned[0].out_path == tmp_path / '01 - Title (2).flac'
    assert versioned[1].out_path == fresh

    overwritten = RipEngine._apply_existing_files_policy(dests, 'overwrite')
    assert [d.out_path for d in overwritten] == [existing, fresh]


def test_conflicting_paths(tmp_path: Path) -> None:
    from ready2rip.rip.engine import RipEngine

    a = tmp_path / 'a.flac'
    b = tmp_path / 'b.flac'
    a.write_bytes(b'1')
    assert RipEngine.conflicting_paths([a, b]) == [a]
