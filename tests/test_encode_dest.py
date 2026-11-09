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
