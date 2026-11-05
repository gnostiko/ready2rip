# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for CUE sheets, CD-TEXT parsing, and MusicBrainz genre pick."""

from __future__ import annotations

from pathlib import Path

from ready2rip.disc.cdmeta import (
    enrich_album_from_disc,
    parse_cd_text,
    parse_isrc_response,
    parse_mcn_response,
    read_cd_text,
    read_disc_codes,
    read_isrc,
    read_mcn,
)
from ready2rip.disc.probe import DiscInfo, TrackInfo
from ready2rip.metadata.providers import AlbumMetadata, TrackMetadata, _mb_primary_genre
from ready2rip.rip.cue import multi_file_cue_basename, write_multi_file_cue_sheet


def _disc_with_gap() -> DiscInfo:
    return DiscInfo(
        device='/dev/sr0',
        tracks=[
            TrackInfo(number=1, start_sector=0, length_sectors=1000, pregap_sectors=150),
            TrackInfo(number=2, start_sector=1000, length_sectors=800, pregap_sectors=75),
        ],
    )


def test_multi_file_cue_uses_index00_for_intertrack_gap(tmp_path: Path) -> None:
    """Multi-file CUE: next-track INDEX 00 lives in the previous FILE."""
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
        album=AlbumMetadata(
            title='A',
            artist='B',
            barcode='0123456789012',
            tracks=[
                TrackMetadata(number=1, title='One', isrc='USRC17607839'),
                TrackMetadata(number=2, title='Two'),
            ],
        ),
    )
    text = cue.read_text(encoding='utf-8')
    assert 'INDEX 00' in text
    assert 'TRACK 01 AUDIO' in text
    assert 'TRACK 02 AUDIO' in text
    assert 'CATALOG 0123456789012' in text
    assert 'ISRC USRC17607839' in text
    # Track-1 pause not stored in a previous file → PREGAP on track 1.
    assert 'PREGAP' in text


def test_multi_disc_cue_basename_is_cd_n() -> None:
    album = AlbumMetadata(
        title='Box',
        artist='Act',
        medium_position=2,
        medium_count=3,
    )
    assert multi_file_cue_basename(album, None) == 'CD2'
    single = AlbumMetadata(title='Box', artist='Act', medium_count=1)
    assert multi_file_cue_basename(single, None) == 'Act - Box'


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


def test_parse_mcn_and_isrc_responses() -> None:
    mcn = bytearray(24)
    mcn[4] = 0x02
    mcn[5] = 0x80  # MCVal
    mcn[8:21] = b'0123456789012'
    assert parse_mcn_response(bytes(mcn)) == '0123456789012'

    isrc = bytearray(24)
    isrc[4] = 0x03
    isrc[5] = 0x80  # TCVal
    isrc[8:20] = b'USRC17607839'
    assert parse_isrc_response(bytes(isrc)) == 'USRC17607839'

    bad = bytearray(24)
    bad[4] = 0x02
    bad[5] = 0x00  # invalid
    assert parse_mcn_response(bytes(bad)) == ''


def test_cdmeta_failed_device_returns_empty_without_inventing() -> None:
    """Unreadable devices yield empty results; existing album is unchanged."""
    album = AlbumMetadata(title='Keep', artist='Me')
    assert read_cd_text('/dev/null') is None
    assert read_mcn('/dev/null') == ''
    assert read_isrc('/dev/null', 1) == ''
    codes = read_disc_codes('/dev/null', [1, 2])
    assert codes.mcn == ''
    assert codes.isrcs == {}
    assert (
        enrich_album_from_disc(album, device='/dev/null', track_numbers=[1])
        is album
    )


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
