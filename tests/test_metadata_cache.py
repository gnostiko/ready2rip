# SPDX-License-Identifier: GPL-3.0-or-later
"""Metadata cache prune / LRU behaviour."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ready2rip.artwork.fetch import ArtworkImage
from ready2rip.disc.probe import DiscInfo, TrackInfo
from ready2rip.metadata.cache import MetadataCache
from ready2rip.metadata.providers import AlbumMetadata
from ready2rip.util import MAX_DOWNLOAD_BYTES


def _disc(freedb: str) -> DiscInfo:
    return DiscInfo(
        device='/dev/sr0',
        freedb_id=freedb,
        tracks=[TrackInfo(number=1, length_sectors=1000, start_sector=0)],
    )


def _art(payload: bytes, name: str = 'local') -> ArtworkImage:
    # Minimal valid JPEG SOI/EOI so mime helpers stay happy if touched.
    data = payload if payload else b'\xff\xd8\xff\xd9'
    return ArtworkImage(
        data=data,
        mime='image/jpeg',
        width=10,
        height=10,
        source=name,
    )


def test_artwork_limit_is_100_mb() -> None:
    assert MAX_DOWNLOAD_BYTES == 100 * 1024 * 1024


def test_prune_noop_under_budget(tmp_path: Path) -> None:
    cache = MetadataCache(tmp_path, max_bytes=10_000_000, prune_on_init=False)
    cache.save(
        _disc('aaaa1111'),
        AlbumMetadata(title='A', artist='X'),
        artwork=_art(b'\xff\xd8' + b'a' * 100 + b'\xff\xd9'),
    )
    assert cache.prune() == 0
    assert list(tmp_path.glob('*.json'))


def test_prune_drops_oldest_when_over_budget(tmp_path: Path) -> None:
    cache = MetadataCache(tmp_path, max_bytes=50_000_000, prune_on_init=False)
    old = _disc('bbbb2222')
    new = _disc('cccc3333')
    blob = 8000
    cache.save(old, AlbumMetadata(title='Old'), artwork=_art(b'\xff\xd8' + b'o' * blob + b'\xff\xd9'))
    cache.save(new, AlbumMetadata(title='New'), artwork=_art(b'\xff\xd8' + b'n' * blob + b'\xff\xd9'))

    # Force Old to be older in saved_at.
    for path in tmp_path.glob('*.json'):
        payload = json.loads(path.read_text(encoding='utf-8'))
        if payload.get('album', {}).get('title') == 'Old':
            payload['saved_at'] = (datetime.now(UTC) - timedelta(days=2)).isoformat()
            path.write_text(json.dumps(payload), encoding='utf-8')

    cache.max_bytes = blob + 1500  # fits one entry (~8.7 KiB), not two
    removed = cache.prune()
    assert removed >= 1
    titles = []
    for path in tmp_path.glob('*.json'):
        titles.append(json.loads(path.read_text(encoding='utf-8'))['album']['title'])
    assert 'New' in titles
    assert 'Old' not in titles


def test_prune_prefers_aged_entries_when_over_budget(tmp_path: Path) -> None:
    cache = MetadataCache(
        tmp_path,
        max_bytes=50_000_000,
        max_age=timedelta(days=90),
        prune_on_init=False,
    )
    blob = 8000
    aged = _disc('dddd4444')
    recent_small = _disc('eeee5555')
    cache.save(
        aged,
        AlbumMetadata(title='Aged'),
        artwork=_art(b'\xff\xd8' + b'a' * blob + b'\xff\xd9'),
    )
    cache.save(
        recent_small,
        AlbumMetadata(title='Recent'),
        artwork=_art(b'\xff\xd8' + b'r' * blob + b'\xff\xd9'),
    )
    for path in tmp_path.glob('*.json'):
        payload = json.loads(path.read_text(encoding='utf-8'))
        if payload.get('album', {}).get('title') == 'Aged':
            payload['saved_at'] = (datetime.now(UTC) - timedelta(days=120)).isoformat()
            path.write_text(json.dumps(payload), encoding='utf-8')
        else:
            payload['saved_at'] = datetime.now(UTC).isoformat()
            path.write_text(json.dumps(payload), encoding='utf-8')

    cache.max_bytes = blob + 1500  # fits one entry (~8.7 KiB), not two
    removed = cache.prune()
    assert removed >= 1
    titles = [
        json.loads(p.read_text(encoding='utf-8'))['album']['title']
        for p in tmp_path.glob('*.json')
    ]
    assert 'Recent' in titles
    assert 'Aged' not in titles


def test_age_alone_does_not_prune_under_budget(tmp_path: Path) -> None:
    cache = MetadataCache(
        tmp_path,
        max_bytes=10_000_000,
        max_age=timedelta(days=90),
        prune_on_init=False,
    )
    cache.save(
        _disc('ffff6666'),
        AlbumMetadata(title='Ancient'),
        artwork=_art(b'\xff\xd8' + b'x' * 50 + b'\xff\xd9'),
    )
    path = next(tmp_path.glob('*.json'))
    payload = json.loads(path.read_text(encoding='utf-8'))
    payload['saved_at'] = (datetime.now(UTC) - timedelta(days=400)).isoformat()
    path.write_text(json.dumps(payload), encoding='utf-8')

    assert cache.prune() == 0
    assert path.is_file()


def test_load_touches_saved_at_for_lru(tmp_path: Path) -> None:
    cache = MetadataCache(tmp_path, prune_on_init=False)
    info = _disc('gggg7777')
    cache.save(info, AlbumMetadata(title='Touch Me'))
    path = next(tmp_path.glob('*.json'))
    payload = json.loads(path.read_text(encoding='utf-8'))
    payload['saved_at'] = (datetime.now(UTC) - timedelta(days=30)).isoformat()
    path.write_text(json.dumps(payload), encoding='utf-8')

    album, _art_out = cache.load(info)
    assert album is not None
    refreshed = json.loads(path.read_text(encoding='utf-8'))['saved_at']
    age = datetime.now(UTC) - datetime.fromisoformat(refreshed.replace('Z', '+00:00'))
    assert age < timedelta(minutes=1)


def test_prune_removes_orphan_covers(tmp_path: Path) -> None:
    cache = MetadataCache(tmp_path, max_bytes=10_000_000, prune_on_init=False)
    orphan = tmp_path / 'covers' / 'orphan.jpg'
    orphan.write_bytes(b'\xff\xd8\xff\xd9')
    assert cache.prune() == 0
    assert not orphan.exists()
