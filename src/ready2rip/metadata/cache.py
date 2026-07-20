# SPDX-License-Identifier: GPL-3.0-or-later
"""Persistent per-disc metadata cache (survives app restarts)."""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import asdict, fields
from datetime import datetime, timezone
from pathlib import Path

from ready2rip.artwork.fetch import ArtworkImage
from ready2rip.disc.discid_util import DiscIdentifiers
from ready2rip.disc.probe import DiscInfo
from ready2rip.metadata.providers import AlbumMetadata, TrackMetadata

log = logging.getLogger(__name__)

_CACHE_VERSION = 1
_SAFE_KEY = re.compile(r'[^A-Za-z0-9._-]+')


def cache_root() -> Path:
    base = os.environ.get('XDG_CACHE_HOME')
    if base:
        root = Path(base) / 'ready2rip' / 'metadata'
    else:
        root = Path.home() / '.cache' / 'ready2rip' / 'metadata'
    root.mkdir(parents=True, exist_ok=True)
    (root / 'covers').mkdir(parents=True, exist_ok=True)
    return root


def disc_cache_key(
    info: DiscInfo,
    ids: DiscIdentifiers | None = None,
) -> str:
    """Stable key for a physical disc (prefer MusicBrainz DiscID)."""
    if ids and ids.musicbrainz_discid:
        return f'mb-{ids.musicbrainz_discid}'
    if ids and ids.freedb_id:
        return f'cddb-{ids.freedb_id}'
    if info.freedb_id:
        return f'cddb-{info.freedb_id}'
    # TOC fingerprint from starts + lengths
    parts = [f'{t.number}:{t.start_sector}:{t.length_sectors}' for t in info.tracks]
    raw = '|'.join(parts)
    return 'toc-' + _SAFE_KEY.sub('_', raw)[:120]


def _safe_filename(key: str) -> str:
    return _SAFE_KEY.sub('_', key)[:180] or 'unknown'


def _album_to_dict(album: AlbumMetadata) -> dict:
    data = asdict(album)
    return data


def _album_from_dict(data: dict) -> AlbumMetadata:
    known = {f.name for f in fields(AlbumMetadata)}
    tracks_raw = data.get('tracks') or []
    tracks: list[TrackMetadata] = []
    t_known = {f.name for f in fields(TrackMetadata)}
    for t in tracks_raw:
        if not isinstance(t, dict):
            continue
        tracks.append(
            TrackMetadata(**{k: v for k, v in t.items() if k in t_known})
        )
    clean = {k: v for k, v in data.items() if k in known and k != 'tracks'}
    return AlbumMetadata(tracks=tracks, **clean)


class MetadataCache:
    """Read/write album metadata (+ optional cover) keyed by disc identity."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or cache_root()
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / 'covers').mkdir(parents=True, exist_ok=True)

    def _json_path(self, key: str) -> Path:
        return self.root / f'{_safe_filename(key)}.json'

    def _cover_path(self, key: str, mime: str = 'image/jpeg') -> Path:
        ext = {
            'image/png': '.png',
            'image/webp': '.webp',
            'image/gif': '.gif',
            'image/jpeg': '.jpg',
        }.get(mime, '.jpg')
        return self.root / 'covers' / f'{_safe_filename(key)}{ext}'

    def load(
        self,
        info: DiscInfo,
        ids: DiscIdentifiers | None = None,
    ) -> tuple[AlbumMetadata | None, ArtworkImage | None]:
        key = disc_cache_key(info, ids)
        path = self._json_path(key)
        if not path.is_file():
            return None, None
        try:
            payload = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning('Failed to read metadata cache %s: %s', path, exc)
            return None, None

        if int(payload.get('version', 0)) != _CACHE_VERSION:
            log.info('Ignoring outdated metadata cache %s', path)
            return None, None

        album_data = payload.get('album')
        if not isinstance(album_data, dict):
            return None, None
        try:
            album = _album_from_dict(album_data)
        except TypeError as exc:
            log.warning('Invalid album cache entry: %s', exc)
            return None, None

        art: ArtworkImage | None = None
        cover_rel = payload.get('artwork_file')
        if cover_rel:
            cover_path = self.root / cover_rel
            if cover_path.is_file():
                try:
                    data = cover_path.read_bytes()
                    mime = payload.get('artwork_mime') or 'image/jpeg'
                    width = int(payload.get('artwork_width') or 0)
                    height = int(payload.get('artwork_height') or 0)
                    if width <= 0 or height <= 0:
                        from ready2rip.artwork.fetch import _image_size

                        width, height = _image_size(data)
                    if width > 0 and height > 0:
                        art = ArtworkImage(
                            data=data,
                            mime=mime,
                            width=width,
                            height=height,
                            source=payload.get('artwork_source') or 'cache',
                            url=str(cover_path),
                        )
                except OSError as exc:
                    log.warning('Failed to load cached cover %s: %s', cover_path, exc)

        log.info('Loaded metadata cache for %s', key)
        return album, art

    def save(
        self,
        info: DiscInfo,
        album: AlbumMetadata,
        ids: DiscIdentifiers | None = None,
        artwork: ArtworkImage | None = None,
    ) -> None:
        key = disc_cache_key(info, ids)
        cover_rel = None
        art_meta: dict = {}
        if artwork is not None and artwork.data:
            # Remove previous cover variants for this key.
            for old in (self.root / 'covers').glob(f'{_safe_filename(key)}.*'):
                try:
                    old.unlink()
                except OSError:
                    pass
            cover_path = self._cover_path(key, artwork.mime)
            try:
                cover_path.write_bytes(artwork.data)
                cover_rel = str(cover_path.relative_to(self.root))
                art_meta = {
                    'artwork_file': cover_rel,
                    'artwork_mime': artwork.mime,
                    'artwork_width': artwork.width,
                    'artwork_height': artwork.height,
                    'artwork_source': artwork.source,
                }
            except OSError as exc:
                log.warning('Failed to cache cover art: %s', exc)

        payload = {
            'version': _CACHE_VERSION,
            'key': key,
            'saved_at': datetime.now(timezone.utc).isoformat(),
            'device': info.device,
            'track_count': info.track_count,
            'album': _album_to_dict(album),
            **art_meta,
        }
        path = self._json_path(key)
        try:
            path.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False) + '\n',
                encoding='utf-8',
            )
            log.debug('Saved metadata cache %s', path)
        except OSError as exc:
            log.warning('Failed to write metadata cache %s: %s', path, exc)

    def has_useful_metadata(self, album: AlbumMetadata | None) -> bool:
        if album is None:
            return False
        if album.title or album.artist:
            return True
        return any(t.title or t.artist for t in album.tracks)
