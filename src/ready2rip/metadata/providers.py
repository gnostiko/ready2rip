# SPDX-License-Identifier: GPL-3.0-or-later
"""Metadata providers: MusicBrainz and FreeDB/gnudb."""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from ready2rip.util import is_safe_http_url, read_limited

log = logging.getLogger(__name__)

USER_AGENT = 'ready2rip/0.1.0 ( https://github.com/gnostiko/ready2rip )'
MB_BASE = 'https://musicbrainz.org/ws/2'
# gnudb HTTP API (FreeDB-compatible)
GNUDB_BASE = 'https://gnudb.gnudb.org/gnudb'


@dataclass
class TrackMetadata:
    number: int
    title: str = ''
    artist: str = ''
    duration_ms: int | None = None
    musicbrainz_recording_id: str = ''


@dataclass
class AlbumMetadata:
    """Normalized album/release metadata used by the tagger."""

    title: str = ''
    artist: str = ''
    date: str = ''
    barcode: str = ''
    label: str = ''
    catalog_number: str = ''
    musicbrainz_release_id: str = ''
    musicbrainz_release_group_id: str = ''
    discid: str = ''
    tracks: list[TrackMetadata] = field(default_factory=list)
    cover_url: str = ''
    source: str = ''  # musicbrainz | freedb | manual
    country: str = ''
    status: str = ''
    disambiguation: str = ''
    medium_count: int = 1
    medium_position: int = 1

    @property
    def display_label(self) -> str:
        bits = [self.artist or 'Unknown Artist', self.title or 'Unknown Album']
        extra = []
        if self.date:
            extra.append(self.date[:4] if len(self.date) >= 4 else self.date)
        if self.country:
            extra.append(self.country)
        if self.disambiguation:
            extra.append(self.disambiguation)
        if self.status and self.status.lower() != 'official':
            extra.append(self.status)
        if extra:
            return f'{" – ".join(bits)} ({", ".join(extra)})'
        return ' – '.join(bits)


class MetadataProvider(ABC):
    @abstractmethod
    def lookup_by_discid(self, discid: str) -> list[AlbumMetadata]:
        """Return zero or more candidate albums for a MusicBrainz DiscID."""


class MusicBrainzProvider(MetadataProvider):
    """MusicBrainz Web Service v2 (JSON)."""

    def __init__(self, user_agent: str = USER_AGENT, timeout: float = 20.0) -> None:
        self.user_agent = user_agent
        self.timeout = timeout

    def lookup_by_discid(self, discid: str) -> list[AlbumMetadata]:
        if not discid:
            return []
        params = {
            'fmt': 'json',
            'inc': 'artists+recordings+release-groups+labels+artist-credits',
        }
        url = f'{MB_BASE}/discid/{urllib.parse.quote(discid)}?{urllib.parse.urlencode(params)}'
        data = self._get_json(url)
        if not data:
            return []

        releases = data.get('releases') or []
        # When the discid is not in MB, API may return an empty list or error object.
        albums: list[AlbumMetadata] = []
        for release in releases:
            album = self._release_to_album(release, discid)
            if album is not None:
                albums.append(album)
        return albums

    def search(self, artist: str, album: str, limit: int = 10) -> list[AlbumMetadata]:
        query_parts = []
        if artist:
            query_parts.append(f'artist:"{artist}"')
        if album:
            query_parts.append(f'release:"{album}"')
        if not query_parts:
            return []
        params = {
            'query': ' AND '.join(query_parts),
            'fmt': 'json',
            'limit': str(limit),
        }
        url = f'{MB_BASE}/release?{urllib.parse.urlencode(params)}'
        data = self._get_json(url)
        if not data:
            return []
        albums: list[AlbumMetadata] = []
        for release in data.get('releases') or []:
            # Search results are shallow; fetch full release when possible.
            rid = release.get('id')
            if rid:
                full = self.get_release(rid)
                if full is not None:
                    albums.append(full)
                    continue
            album = self._release_to_album(release, discid='')
            if album is not None:
                albums.append(album)
        return albums

    def get_release(self, release_id: str) -> AlbumMetadata | None:
        params = {
            'fmt': 'json',
            'inc': 'artists+recordings+release-groups+labels+artist-credits+media',
        }
        url = f'{MB_BASE}/release/{urllib.parse.quote(release_id)}?{urllib.parse.urlencode(params)}'
        data = self._get_json(url)
        if not data:
            return None
        return self._release_to_album(data, discid='')

    def _get_json(self, url: str) -> dict[str, Any] | None:
        if not is_safe_http_url(url):
            log.warning('Refusing non-http(s) metadata URL: %s', url)
            return None
        request = urllib.request.Request(
            url,
            headers={
                'User-Agent': self.user_agent,
                'Accept': 'application/json',
            },
            method='GET',
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                final = response.geturl()
                if final and not is_safe_http_url(final):
                    log.warning('Refusing metadata redirect to unsafe URL: %s', final)
                    return None
                body = read_limited(response).decode('utf-8')
                return json.loads(body)
        except urllib.error.HTTPError as exc:
            log.warning('MusicBrainz HTTP %s for %s', exc.code, url)
            try:
                exc.read()
                exc.close()
            except Exception:  # noqa: BLE001
                pass
            return None
        except (
            urllib.error.URLError,
            TimeoutError,
            json.JSONDecodeError,
            OSError,
            ValueError,
        ) as exc:
            log.warning('MusicBrainz request failed: %s', exc)
            return None

    def _release_to_album(self, release: dict[str, Any], discid: str) -> AlbumMetadata | None:
        if not release:
            return None

        title = release.get('title') or ''
        artist = _artist_credit(release.get('artist-credit'))
        date = release.get('date') or ''
        barcode = release.get('barcode') or ''
        country = release.get('country') or ''
        status = release.get('status') or ''
        disambiguation = release.get('disambiguation') or ''
        release_id = release.get('id') or ''

        rg = release.get('release-group') or {}
        rg_id = rg.get('id') or ''

        label = ''
        catalog = ''
        label_info = release.get('label-info') or []
        if label_info:
            first = label_info[0] or {}
            catalog = first.get('catalog-number') or ''
            lab = first.get('label') or {}
            label = lab.get('name') or ''

        tracks: list[TrackMetadata] = []
        media = release.get('media') or []
        medium_position = 1
        # Prefer the medium that lists this discid, else first medium.
        chosen = None
        for medium in media:
            discs = medium.get('discs') or []
            if any(d.get('id') == discid for d in discs if discid):
                chosen = medium
                medium_position = int(medium.get('position') or 1)
                break
        if chosen is None and media:
            chosen = media[0]
            medium_position = int(chosen.get('position') or 1)

        if chosen is not None:
            for track in chosen.get('tracks') or []:
                recording = track.get('recording') or {}
                number_raw = track.get('number') or track.get('position') or '0'
                try:
                    number = int(re.sub(r'\D', '', str(number_raw)) or track.get('position') or 0)
                except (TypeError, ValueError):
                    number = int(track.get('position') or 0)
                length = recording.get('length')
                if length is None:
                    length = track.get('length')
                tracks.append(
                    TrackMetadata(
                        number=number,
                        title=recording.get('title') or track.get('title') or '',
                        artist=_artist_credit(
                            track.get('artist-credit') or recording.get('artist-credit')
                        )
                        or artist,
                        duration_ms=int(length) if length is not None else None,
                        musicbrainz_recording_id=recording.get('id') or '',
                    )
                )

        # cover_url left empty: ArtworkFetcher uses musicbrainz_release_id +
        # Cover Art Archive JSON to download the original front image.

        return AlbumMetadata(
            title=title,
            artist=artist,
            date=date,
            barcode=barcode,
            label=label,
            catalog_number=catalog,
            musicbrainz_release_id=release_id,
            musicbrainz_release_group_id=rg_id,
            discid=discid,
            tracks=tracks,
            cover_url='',
            source='musicbrainz',
            country=country,
            status=status,
            disambiguation=disambiguation,
            medium_count=len(media) if media else 1,
            medium_position=medium_position,
        )


class FreeDBProvider(MetadataProvider):
    """FreeDB-compatible lookup via gnudb.org HTTP.

    ``discid`` here is the 8-hex FreeDB ID, not the MusicBrainz DiscID.
    """

    def __init__(
        self,
        base_url: str = GNUDB_BASE,
        user_agent: str = USER_AGENT,
        timeout: float = 15.0,
    ) -> None:
        self.base_url = base_url.rstrip('/')
        self.user_agent = user_agent
        self.timeout = timeout

    def lookup_by_discid(self, discid: str) -> list[AlbumMetadata]:
        """Look up FreeDB ID. Also accepts ``category/id`` paths."""
        if not discid:
            return []

        # If we only have the ID, try common categories via query.
        if '/' not in discid:
            return self._lookup_id_only(discid)

        return self._fetch_entry(discid)

    def _lookup_id_only(self, freedb_id: str) -> list[AlbumMetadata]:
        # gnudb cddb query protocol over HTTP is awkward; try genre paths.
        categories = (
            'rock',
            'pop',
            'blues',
            'classical',
            'country',
            'data',
            'folk',
            'jazz',
            'misc',
            'newage',
            'reggae',
            'soundtrack',
        )
        results: list[AlbumMetadata] = []
        for category in categories:
            found = self._fetch_entry(f'{category}/{freedb_id}')
            results.extend(found)
            if results:
                # One hit is enough for first pass; still return all found in this category.
                break
        return results

    def _fetch_entry(self, path: str) -> list[AlbumMetadata]:
        # Keep path relative; never allow scheme injection via FreeDB id.
        safe = path.lstrip('/')
        if '..' in safe.split('/') or safe.startswith('http'):
            return []
        url = f'{self.base_url}/{safe}'
        if not is_safe_http_url(url):
            return []
        request = urllib.request.Request(
            url,
            headers={'User-Agent': self.user_agent},
            method='GET',
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                final = response.geturl()
                if final and not is_safe_http_url(final):
                    return []
                text = read_limited(response).decode('utf-8', errors='replace')
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                log.debug('gnudb HTTP %s for %s', exc.code, url)
            return []
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            log.warning('gnudb request failed: %s', exc)
            return []

        album = _parse_cddb_entry(text, source='freedb')
        return [album] if album else []


def _artist_credit(credit: Any) -> str:
    if not credit:
        return ''
    if isinstance(credit, str):
        return credit
    parts: list[str] = []
    for item in credit:
        if not isinstance(item, dict):
            continue
        name = item.get('name')
        if not name and isinstance(item.get('artist'), dict):
            name = item['artist'].get('name')
        if name:
            parts.append(name)
        joinphrase = item.get('joinphrase')
        if joinphrase:
            parts.append(joinphrase)
    return ''.join(parts).strip()


def _parse_cddb_entry(text: str, source: str = 'freedb') -> AlbumMetadata | None:
    """Parse a classic CDDB/FreeDB entry body into AlbumMetadata."""
    if not text or 'DTITLE=' not in text:
        return None

    dtitle = ''
    dyear = ''
    dgenre = ''
    tracks: dict[int, str] = {}

    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith('#'):
            continue
        if line.startswith('DTITLE='):
            dtitle += line[7:]
        elif line.startswith('DYEAR='):
            dyear = line[6:].strip()
        elif line.startswith('DGENRE='):
            dgenre = line[7:].strip()
        else:
            match = re.match(r'TTITLE(\d+)=(.*)', line)
            if match:
                idx = int(match.group(1))
                tracks[idx] = tracks.get(idx, '') + match.group(2)

    artist = ''
    title = dtitle
    if ' / ' in dtitle:
        artist, title = dtitle.split(' / ', 1)

    track_list = [
        TrackMetadata(number=i + 1, title=tracks[i], artist=artist)
        for i in sorted(tracks)
    ]
    if not title and not track_list:
        return None

    return AlbumMetadata(
        title=title.strip(),
        artist=artist.strip(),
        date=dyear,
        tracks=track_list,
        source=source,
        disambiguation=dgenre,
    )


def lookup_metadata(
    musicbrainz_discid: str | None,
    freedb_id: str | None,
    *,
    use_musicbrainz: bool = True,
    use_freedb: bool = True,
) -> list[AlbumMetadata]:
    """Query enabled providers and return combined candidates (MB first)."""
    results: list[AlbumMetadata] = []
    seen_keys: set[str] = set()

    def _add(items: list[AlbumMetadata]) -> None:
        for album in items:
            key = (
                album.musicbrainz_release_id
                or f'{album.source}:{album.artist}:{album.title}:{len(album.tracks)}'
            )
            if key in seen_keys:
                continue
            seen_keys.add(key)
            results.append(album)

    if use_musicbrainz and musicbrainz_discid:
        try:
            _add(MusicBrainzProvider().lookup_by_discid(musicbrainz_discid))
        except Exception:  # noqa: BLE001
            log.exception('MusicBrainz lookup failed')

    if use_freedb and freedb_id:
        try:
            _add(FreeDBProvider().lookup_by_discid(freedb_id))
        except Exception:  # noqa: BLE001
            log.exception('FreeDB lookup failed')

    return results
