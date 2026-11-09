# SPDX-License-Identifier: GPL-3.0-or-later
"""LRCLIB lyrics helpers and client matching (no network)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from ready2rip.disc.probe import DiscInfo, TrackInfo
from ready2rip.metadata.lyrics import (
    LrclibClient,
    LyricsResult,
    _best_search_hit,
    embed_lyrics_text,
    plain_from_lrc,
    sectors_to_duration_seconds,
)
from ready2rip.metadata.providers import AlbumMetadata, TrackMetadata
from ready2rip.tags.writer import TagWriter


def test_plain_from_lrc_strips_timestamps() -> None:
    lrc = '[00:12.00]Line one\n[00:15.50]Line two\n[ar:Someone]\nBare line\n'
    assert plain_from_lrc(lrc) == 'Line one\nLine two\nBare line'


def test_embed_lyrics_text_falls_back_to_synced() -> None:
    track = TrackMetadata(
        number=1,
        lyrics='',
        lyrics_synced='[00:01.00]Hello\n[00:02.00]World',
    )
    assert embed_lyrics_text(track) == 'Hello\nWorld'
    track.lyrics = 'Plain'
    assert embed_lyrics_text(track) == 'Plain'


def test_sectors_to_duration() -> None:
    assert sectors_to_duration_seconds(7500) == 100.0


def test_best_search_hit_prefers_duration_and_album() -> None:
    hits = [
        {
            'albumName': 'Wrong',
            'duration': 200,
            'plainLyrics': 'a',
        },
        {
            'albumName': 'Right',
            'duration': 101,
            'plainLyrics': 'b',
        },
    ]
    result = _best_search_hit(hits, album='Right', duration=100.0)
    assert result is not None
    assert result.plain == 'b'


def test_fetch_for_track_get_then_search(monkeypatch) -> None:
    client = LrclibClient(min_interval=0.0)
    calls: list[str] = []

    def fake_json(url: str, *, allow_404: bool = False, attempt: int = 0):
        calls.append(url)
        if '/api/get?' in url:
            return None
        if '/api/search?' in url:
            return [
                {
                    'albumName': 'Album',
                    'duration': 120.0,
                    'plainLyrics': 'Found it',
                    'syncedLyrics': None,
                    'instrumental': False,
                }
            ]
        return None

    monkeypatch.setattr(client, '_request_json', fake_json)
    result = client.fetch_for_track(
        artist='Artist',
        title='Song',
        album='Album',
        duration=120.0,
    )
    assert result is not None
    assert result.plain == 'Found it'
    assert any('/api/get?' in u for u in calls)
    assert any('/api/search?' in u for u in calls)


def test_fetch_album_fills_tracks() -> None:
    client = LrclibClient(min_interval=0.0)
    album = AlbumMetadata(
        title='Album',
        artist='Artist',
        tracks=[
            TrackMetadata(number=1, title='One', artist='Artist'),
            TrackMetadata(number=2, title='Two', artist='Artist', lyrics='Keep'),
        ],
    )
    disc = DiscInfo(
        device='/dev/sr0',
        tracks=[
            TrackInfo(number=1, length_sectors=7500, start_sector=0),
            TrackInfo(number=2, length_sectors=8000, start_sector=7500),
        ],
    )

    def fake_fetch(**kwargs):
        return LyricsResult(plain=f'Lyrics for {kwargs["title"]}')

    with patch.object(client, 'fetch_for_track', side_effect=fake_fetch):
        updated = client.fetch_album(album, disc, only_missing=True)
    assert updated == 1
    assert album.tracks[0].lyrics == 'Lyrics for One'
    assert album.tracks[1].lyrics == 'Keep'


def test_write_lyrics_sidecar_lrc_and_txt(tmp_path: Path) -> None:
    from ready2rip.metadata.lyrics import write_lyrics_sidecar

    audio = tmp_path / '01 - Song.flac'
    audio.write_bytes(b'')
    synced = TrackMetadata(
        number=1,
        lyrics='Hello',
        lyrics_synced='[00:01.00]Hello\n',
    )
    lrc = write_lyrics_sidecar(audio, synced)
    assert lrc is not None and lrc.suffix == '.lrc'
    assert '[00:01.00]Hello' in lrc.read_text(encoding='utf-8')

    plain_only = TrackMetadata(number=2, lyrics='Just text')
    txt = write_lyrics_sidecar(audio, plain_only)
    assert txt is not None and txt.suffix == '.txt'
    assert txt.read_text(encoding='utf-8').startswith('Just text')


def test_apply_vorbis_writes_lyrics() -> None:
    audio: dict[str, list[str]] = {}
    TagWriter()._apply_vorbis(
        audio,
        title='Song',
        artists=['A'],
        album='Album',
        album_artists=['A'],
        track_number=1,
        total_tracks=1,
        lyrics='Hello\nWorld',
        synced_lyrics='[00:01.00]Hello',
    )
    assert audio['LYRICS'] == ['Hello\nWorld']
    assert audio['UNSYNCEDLYRICS'] == ['Hello\nWorld']
    assert audio['SYNCEDLYRICS'] == ['[00:01.00]Hello']


def test_lyrics_save_mode_helpers() -> None:
    from ready2rip.settings import (
        lyrics_mode_writes_sidecar,
        lyrics_mode_writes_tags,
        normalize_lyrics_save_mode,
    )

    assert normalize_lyrics_save_mode('EMBED') == 'embed'
    assert normalize_lyrics_save_mode('nope') == 'both'
    assert lyrics_mode_writes_tags('embed') and not lyrics_mode_writes_sidecar('embed')
    assert lyrics_mode_writes_sidecar('sidecar') and not lyrics_mode_writes_tags('sidecar')
    assert lyrics_mode_writes_tags('both') and lyrics_mode_writes_sidecar('both')
