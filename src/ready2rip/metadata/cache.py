# SPDX-License-Identifier: GPL-3.0-or-later
"""Persistent per-disc metadata cache (survives app restarts)."""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import asdict, dataclass, fields
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ready2rip.artwork.fetch import ArtworkImage, image_size
from ready2rip.disc.discid_util import DiscIdentifiers
from ready2rip.disc.probe import DiscInfo
from ready2rip.metadata.providers import AlbumMetadata, TrackMetadata
from ready2rip.util import ensure_path_under

log = logging.getLogger(__name__)

_CACHE_VERSION = 1
_SAFE_KEY = re.compile(r'[^A-Za-z0-9._-]+')

# Total on-disk budget for metadata JSON + covers under the cache root.
CACHE_MAX_BYTES = 300 * 1024 * 1024
# When over budget, prefer dropping entries older than this before other LRU.
CACHE_MAX_AGE = timedelta(days=90)


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


def _parse_saved_at(raw: object, fallback: float) -> datetime:
    if isinstance(raw, str) and raw.strip():
        try:
            text = raw.strip().replace('Z', '+00:00')
            dt = datetime.fromisoformat(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt.astimezone(UTC)
        except ValueError:
            pass
    return datetime.fromtimestamp(fallback, tz=UTC)


@dataclass
class _CacheEntry:
    json_path: Path
    cover_paths: list[Path]
    saved_at: datetime
    size: int


class MetadataCache:
    """Read/write album metadata (+ optional cover) keyed by disc identity."""

    def __init__(
        self,
        root: Path | None = None,
        *,
        max_bytes: int = CACHE_MAX_BYTES,
        max_age: timedelta = CACHE_MAX_AGE,
        prune_on_init: bool = True,
    ) -> None:
        self.root = root or cache_root()
        self.max_bytes = max(0, int(max_bytes))
        self.max_age = max_age
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / 'covers').mkdir(parents=True, exist_ok=True)
        if prune_on_init:
            self.prune()

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
        if cover_rel and isinstance(cover_rel, str) and not Path(cover_rel).is_absolute():
            try:
                cover_path = ensure_path_under(self.root, self.root / cover_rel)
            except ValueError:
                log.warning('Ignoring cache artwork path outside cache root: %s', cover_rel)
                cover_path = None
            if cover_path is not None and cover_path.is_file():
                try:
                    data = cover_path.read_bytes()
                    mime = payload.get('artwork_mime') or 'image/jpeg'
                    width = int(payload.get('artwork_width') or 0)
                    height = int(payload.get('artwork_height') or 0)
                    if width <= 0 or height <= 0:
                        width, height = image_size(data)
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

        # Touch for LRU so re-used discs survive size pruning.
        self._touch_saved_at(path, payload)
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
            'saved_at': datetime.now(UTC).isoformat(),
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
        self.prune()

    def prune(self) -> int:
        """Delete oldest cache entries until under the size budget.

        Under budget: do nothing (no age-only expiry).
        Over budget: drop entries older than ``max_age`` first (oldest first),
        then remaining oldest by ``saved_at`` until within ``max_bytes``.
        Orphan cover files (no JSON) are removed whenever prune runs.
        Returns the number of disc entries removed.
        """
        covers_dir = self.root / 'covers'
        covers_dir.mkdir(parents=True, exist_ok=True)

        entries = self._list_entries()
        referenced = {p.resolve() for e in entries for p in e.cover_paths}
        orphans_removed = 0
        for cover in covers_dir.iterdir():
            if not cover.is_file():
                continue
            try:
                if cover.resolve() not in referenced:
                    cover.unlink()
                    orphans_removed += 1
            except OSError:
                pass
        if orphans_removed:
            log.info('Removed %s orphan cover(s) from metadata cache', orphans_removed)

        total = sum(e.size for e in entries)
        if self.max_bytes <= 0 or total <= self.max_bytes:
            return 0

        now = datetime.now(UTC)
        aged = [e for e in entries if now - e.saved_at > self.max_age]
        aged.sort(key=lambda e: e.saved_at)
        recent = [e for e in entries if now - e.saved_at <= self.max_age]
        recent.sort(key=lambda e: e.saved_at)
        victims = aged + recent

        removed = 0
        for entry in victims:
            if total <= self.max_bytes:
                break
            freed = self._delete_entry(entry)
            total -= freed
            removed += 1
        if removed:
            log.info(
                'Pruned %s metadata cache entries (budget %s bytes)',
                removed,
                self.max_bytes,
            )
        return removed

    def has_useful_metadata(self, album: AlbumMetadata | None) -> bool:
        if album is None:
            return False
        if album.title or album.artist:
            return True
        return any(t.title or t.artist for t in album.tracks)

    def _touch_saved_at(self, path: Path, payload: dict) -> None:
        payload['saved_at'] = datetime.now(UTC).isoformat()
        try:
            path.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False) + '\n',
                encoding='utf-8',
            )
        except OSError as exc:
            log.debug('Could not touch cache LRU timestamp %s: %s', path, exc)

    def _list_entries(self) -> list[_CacheEntry]:
        entries: list[_CacheEntry] = []
        for path in sorted(self.root.glob('*.json')):
            if not path.is_file():
                continue
            try:
                payload = json.loads(path.read_text(encoding='utf-8'))
            except (OSError, json.JSONDecodeError):
                # Unreadable JSON still counts toward size; delete as a unit.
                size = path.stat().st_size if path.is_file() else 0
                entries.append(
                    _CacheEntry(
                        json_path=path,
                        cover_paths=[],
                        saved_at=datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
                        if path.is_file()
                        else datetime.now(UTC),
                        size=size,
                    )
                )
                continue

            try:
                mtime = path.stat().st_mtime
                size = path.stat().st_size
            except OSError:
                continue
            saved_at = _parse_saved_at(payload.get('saved_at'), mtime)
            covers: list[Path] = []
            cover_rel = payload.get('artwork_file')
            if isinstance(cover_rel, str) and cover_rel and not Path(cover_rel).is_absolute():
                try:
                    cover_path = ensure_path_under(self.root, self.root / cover_rel)
                except ValueError:
                    cover_path = None
                if cover_path is not None and cover_path.is_file():
                    covers.append(cover_path)
                    try:
                        size += cover_path.stat().st_size
                    except OSError:
                        pass
            # Also count any leftover cover variants for this stem.
            stem = path.stem
            for extra in (self.root / 'covers').glob(f'{stem}.*'):
                if extra in covers:
                    continue
                if extra.is_file():
                    covers.append(extra)
                    try:
                        size += extra.stat().st_size
                    except OSError:
                        pass
            entries.append(
                _CacheEntry(
                    json_path=path,
                    cover_paths=covers,
                    saved_at=saved_at,
                    size=size,
                )
            )
        return entries

    def _delete_entry(self, entry: _CacheEntry) -> int:
        freed = 0
        for path in [entry.json_path, *entry.cover_paths]:
            try:
                if path.is_file():
                    freed += path.stat().st_size
                    path.unlink()
            except OSError as exc:
                log.debug('Failed to delete cache file %s: %s', path, exc)
        return freed
