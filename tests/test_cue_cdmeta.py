# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for CUE sheets, CD-TEXT parsing, and MusicBrainz genre pick."""

from __future__ import annotations

from pathlib import Path

from ready2rip.disc.cdmeta import parse_cd_text
from ready2rip.disc.probe import DiscInfo, TrackInfo
from ready2rip.metadata.providers import AlbumMetadata, _mb_primary_genre
from ready2rip.rip.cue import write_multi_file_cue_sheet


def _disc_with_gap() -> DiscInfo:
    return DiscInfo(
        device='/dev/sr0',
        tracks=[
            TrackInfo(number=1, start_sector=0, length_sectors=1000, pregap_sectors=150),
            TrackInfo(number=2, start_sector=1000, length_sectors=800, pregap_sectors=75),
        ],
    )


def test_multi_file_cue_uses_index00_for_intertrack_gap(tmp_path: Path) -> None:
    """EAC-style multi-file CUE: next-track INDEX 00 lives in the previous FILE."""
    disc = _disc_with_gap()
    t1 = tmp_path / '01.flac'
    t2 = tmp_path / '02.flac'
    t1.write_bytes(b'x')
    t2.write_bytes(b'x')
    cue = tmp_path / 'album.cue'
    write_multi_file_cue_sheet(
        cue,
        track_files=[(1, t1), (2, t2)],
        file_type='FLAC',
        disc=disc,
        album=AlbumMetadata(title='A', artist='B'),
    )
    text = cue.read_text(encoding='utf-8')
    assert 'INDEX 00' in text
    assert 'TRACK 01 AUDIO' in text
    assert 'TRACK 02 AUDIO' in text
    # Track-1 pause not stored in a previous file → PREGAP on track 1.
    assert 'PREGAP' in text


def test_parse_cd_text_titles() -> None:
    # Minimal synthetic pack stream: header + one title pack for album.
    from ready2rip.disc.cdmeta import _crc16_ccitt

    payload = b'Album Title\x00'
    pack = bytearray(18)
    pack[0] = 0x80
    pack[1] = 0  # album
    pack[2] = 0
    pack[3] = 0
    pack[4:16] = payload.ljust(12, b'\x00')[:12]
    crc = _crc16_ccitt(bytes(pack[:16])) ^ 0xFFFF
    pack[16] = (crc >> 8) & 0xFF
    pack[17] = crc & 0xFF
    data_len = 2 + 18
    raw = bytes([(data_len >> 8) & 0xFF, data_len & 0xFF, 0, 0]) + bytes(pack)
    info = parse_cd_text(raw)
    assert info.album_title.startswith('Album Title')


def test_mb_primary_genre_picks_highest_count() -> None:
    entity = {
        'genres': [
            {'name': 'rock', 'count': 2},
            {'name': 'indie rock', 'count': 9},
        ]
    }
    assert _mb_primary_genre(entity) == 'Indie Rock'
    assert _mb_primary_genre({}) == ''


def test_mb_primary_genre_falls_back_to_tags() -> None:
    entity = {
        'genres': [],
        'tags': [
            {'name': '1997', 'count': 9},
            {'name': 'shoegaze', 'count': 4},
            {'name': 'dream pop', 'count': 6},
        ],
    }
    assert _mb_primary_genre(entity) == 'Dream Pop'
