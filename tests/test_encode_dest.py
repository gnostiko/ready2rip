# SPDX-License-Identifier: GPL-3.0-or-later
"""Also-encode destination helpers (no PyGObject)."""

from __future__ import annotations

from pathlib import Path

from ready2rip.rip.encode_dest import (
    audio_dir_for_format,
    encode_format_list,
    format_folder_name,
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


def test_encode_format_list_skips_duplicate_and_copy_image() -> None:
    assert encode_format_list(
        'mp3', also_encode=True, also_format='mp3'
    ) == ['mp3']
    assert encode_format_list(
        'flac', also_encode=True, also_format='mp3', allow_also=False
    ) == ['flac']


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


def test_format_folder_name() -> None:
    assert format_folder_name('aac') == 'M4A'
    assert format_folder_name('opus') == 'Opus'
