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

from ready2rip.util import is_safe_http_url, normalize_release_date, read_limited

log = logging.getLogger(__name__)

USER_AGENT = 'ready2rip/0.4.1 ( https://github.com/gnostiko/ready2rip )'
MB_BASE = 'https://musicbrainz.org/ws/2'
# gnudb CDDB HTTP gateway (port 80). HTTPS on gnudb.gnudb.org is often refused.
GNUDB_CGI_URLS = (
    'http://gnudb.gnudb.org/~cddb/cddb.cgi',
    'http://gnudb.org/~cddb/cddb.cgi',
)
# Last-resort FreeDB-style path used by some mirrors.
GNUDB_REST_URLS = (
    'http://gnudb.gnudb.org/gnudb',
)
GNUDB_BASE = GNUDB_REST_URLS[0]

# MusicBrainz release UUID (with optional URL / release: prefix).
_MB_RELEASE_ID_RE = re.compile(
    r'(?:https?://(?:www\.)?musicbrainz\.org/release/)?'
    r'(?:release:)?'
    r'([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})',
    re.IGNORECASE,
)


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

    def search(
        self, artist: str, album_title: str, limit: int = 10
    ) -> list[AlbumMetadata]:
        query_parts = []
        if artist:
            query_parts.append(f'artist:"{artist}"')
        if album_title:
            query_parts.append(f'release:"{album_title}"')
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
            parsed = self._release_to_album(release, discid='')
            if parsed is not None:
                albums.append(parsed)
        return albums

    def get_release(
        self,
        release_id: str,
        *,
        discid: str = '',
        preferred_track_count: int = 0,
    ) -> AlbumMetadata | None:
        params = {
            'fmt': 'json',
            'inc': 'artists+recordings+release-groups+labels+artist-credits+media',
        }
        url = f'{MB_BASE}/release/{urllib.parse.quote(release_id)}?{urllib.parse.urlencode(params)}'
        data = self._get_json(url)
        if not data:
            return None
        return self._release_to_album(
            data,
            discid=discid,
            preferred_track_count=preferred_track_count,
        )

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
            except Exception:
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

    def _release_to_album(
        self,
        release: dict[str, Any],
        discid: str,
        *,
        preferred_track_count: int = 0,
    ) -> AlbumMetadata | None:
        if not release:
            return None

        title = release.get('title') or ''
        artist = _artist_credit(release.get('artist-credit'))
        barcode = release.get('barcode') or ''
        country = release.get('country') or ''
        status = release.get('status') or ''
        disambiguation = release.get('disambiguation') or ''
        release_id = release.get('id') or ''

        rg = release.get('release-group') or {}
        rg_id = rg.get('id') or ''
        # Prefer the most complete of release date vs RG first-release-date
        # (YYYY / YYYY-MM / YYYY-MM-DD).
        date = normalize_release_date(
            release.get('date') or '',
            rg.get('first-release-date') or '',
        )

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
        # Prefer medium with this discid, else matching track count, else first.
        chosen = None
        for medium in media:
            discs = medium.get('discs') or []
            if any(d.get('id') == discid for d in discs if discid):
                chosen = medium
                medium_position = int(medium.get('position') or 1)
                break
        if chosen is None and preferred_track_count > 0 and media:
            for medium in media:
                n = len(medium.get('tracks') or [])
                if n == preferred_track_count:
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
    """FreeDB-compatible lookup via gnudb CDDB HTTP CGI.

    ``discid`` here is the 8-hex FreeDB ID, not the MusicBrainz DiscID.
    Pass MusicBrainz-style TOC *offsets* (leadout first) when available so we
    can issue a proper ``cddb query`` instead of probing every genre path.
    """

    _CDDB_CATEGORIES = (
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

    def __init__(
        self,
        base_url: str = GNUDB_BASE,
        user_agent: str = USER_AGENT,
        timeout: float = 15.0,
        offsets: tuple[int, ...] = (),
        track_count: int = 0,
    ) -> None:
        self.base_url = base_url.rstrip('/')
        self.user_agent = user_agent
        self.timeout = timeout
        self.offsets = offsets
        self.track_count = track_count
        self._cgi_url: str | None = None
        self._network_dead = False

    def lookup_by_discid(self, discid: str) -> list[AlbumMetadata]:
        """Look up FreeDB ID. Also accepts ``category/id`` paths."""
        if not discid:
            return []

        if '/' in discid:
            return self._fetch_rest_entry(discid)

        albums = self._cddb_query_and_read(discid)
        if albums or self._network_dead:
            return albums
        return self._lookup_id_only(discid)

    def _cddb_hello(self) -> str:
        from ready2rip import config

        version = getattr(config, 'APPLICATION_VERSION', '0.4.1')
        return f'anonymous localhost {config.APPLICATION_NAME} {version}'

    def _cddb_query_and_read(self, freedb_id: str) -> list[AlbumMetadata]:
        if len(self.offsets) < 2:
            return []
        ntracks = self.track_count or (len(self.offsets) - 1)
        if ntracks < 1:
            return []
        starts = [int(o) for o in self.offsets[1 : 1 + ntracks]]
        leadout = int(self.offsets[0])
        if not starts or leadout <= starts[-1]:
            return []
        total_sec = max(1, leadout // 75)
        cmd = (
            f'cddb query {freedb_id} {ntracks} '
            f'{" ".join(str(s) for s in starts)} {total_sec}'
        )
        text = self._cddb_request(cmd)
        if not text:
            return []

        matches = _parse_cddb_query_matches(text, fallback_id=freedb_id)
        albums: list[AlbumMetadata] = []
        for category, disc_id in matches[:3]:
            body = self._cddb_request(f'cddb read {category} {disc_id}')
            album = _parse_cddb_entry(body or '', source='freedb')
            if album is not None:
                albums.append(album)
        return albums

    def _cddb_request(self, cmd: str) -> str | None:
        if self._network_dead:
            return None
        params = {
            'cmd': cmd,
            'hello': self._cddb_hello(),
            'proto': '6',
        }
        query = urllib.parse.urlencode(params)
        urls = ((self._cgi_url,) if self._cgi_url else GNUDB_CGI_URLS)
        last_exc: Exception | None = None
        for base in urls:
            if not is_safe_http_url(base):
                continue
            url = f'{base}?{query}'
            text, exc = self._http_get_text(url)
            if text is not None:
                self._cgi_url = base
                return text
            last_exc = exc
            if _is_connection_refused(exc):
                continue
        if last_exc is not None:
            self._network_dead = _is_connection_refused(last_exc)
            log.warning('gnudb request failed: %s', last_exc)
        return None

    def _lookup_id_only(self, freedb_id: str) -> list[AlbumMetadata]:
        results: list[AlbumMetadata] = []
        for category in self._CDDB_CATEGORIES:
            if self._network_dead:
                break
            found = self._fetch_rest_entry(f'{category}/{freedb_id}')
            results.extend(found)
            if results:
                break
        return results

    def _fetch_rest_entry(self, path: str) -> list[AlbumMetadata]:
        # Keep path relative; never allow scheme injection via FreeDB id.
        safe = path.lstrip('/')
        if '..' in safe.split('/') or safe.startswith('http'):
            return []
        last_exc: Exception | None = None
        for base in GNUDB_REST_URLS:
            url = f'{base.rstrip("/")}/{safe}'
            if not is_safe_http_url(url):
                continue
            text, exc = self._http_get_text(url)
            if text is not None:
                album = _parse_cddb_entry(text, source='freedb')
                return [album] if album else []
            last_exc = exc
            if _is_connection_refused(exc):
                self._network_dead = True
                break
        if last_exc is not None:
            log.warning('gnudb request failed: %s', last_exc)
        return []

    def _http_get_text(self, url: str) -> tuple[str | None, Exception | None]:
        request = urllib.request.Request(
            url,
            headers={'User-Agent': self.user_agent},
            method='GET',
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                final = response.geturl()
                if final and not is_safe_http_url(final):
                    return None, ValueError('unsafe redirect')
                text = read_limited(response).decode('utf-8', errors='replace')
                return text, None
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                log.debug('gnudb HTTP %s for %s', exc.code, url)
            return None, exc
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            return None, exc


def _artist_credit(credit: Any) -> str:
    """Build a multi-artist string from MusicBrainz artist-credit.

    Names are joined with ``; `` (dBpoweramp-style multi-value), not MB
    joinphrases like `` feat. `` — those are lost in favour of true multi-artist
    tags when ripping.
    """
    from ready2rip.tags.artists import join_artists, normalize_artists

    if not credit:
        return ''
    if isinstance(credit, str):
        # Already a string — normalize if it used joinphrases/semicolons.
        if ';' in credit:
            return normalize_artists(credit)
        return credit.strip()
    names: list[str] = []
    for item in credit:
        if not isinstance(item, dict):
            continue
        name = item.get('name')
        if not name and isinstance(item.get('artist'), dict):
            name = item['artist'].get('name')
        if name:
            names.append(str(name).strip())
    return join_artists(names)


def _is_connection_refused(exc: Exception | None) -> bool:
    if exc is None:
        return False
    reason = getattr(exc, 'reason', exc)
    errno = getattr(reason, 'errno', None)
    if errno == 111:
        return True
    text = str(exc).casefold()
    return 'connection refused' in text or 'errno 111' in text


def _parse_cddb_query_matches(
    text: str,
    *,
    fallback_id: str,
) -> list[tuple[str, str]]:
    """Parse ``cddb query`` status lines into ``(category, discid)`` pairs."""
    matches: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or line == '.':
            continue
        parts = line.split()
        if parts and parts[0].isdigit():
            parts = parts[1:]
        if len(parts) < 2:
            continue
        category, disc_id = parts[0].lower(), parts[1].lower()
        if not re.fullmatch(r'[a-z]+', category):
            continue
        if not re.fullmatch(r'[0-9a-f]{8}', disc_id):
            disc_id = fallback_id.lower()
            if not re.fullmatch(r'[0-9a-f]{8}', disc_id):
                continue
        key = (category, disc_id)
        if key in seen:
            continue
        seen.add(key)
        matches.append(key)
    return matches


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
            dyear = normalize_release_date(line[6:].strip())
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


def parse_musicbrainz_release_id(text: str) -> str | None:
    """Extract a MusicBrainz *release* UUID from a URL or bare id.

    Accepts forms such as::

        https://musicbrainz.org/release/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
        musicbrainz.org/release/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx/…
        xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx

    Returns ``None`` for empty input, release-group links without a release
    UUID, or unrecognised text.
    """
    if not text or not str(text).strip():
        return None
    raw = str(text).strip()
    # Refuse release-group-only links (different entity).
    if re.search(r'musicbrainz\.org/release-group/', raw, re.I):
        if not re.search(r'musicbrainz\.org/release/[0-9a-f-]{36}', raw, re.I):
            return None
    match = _MB_RELEASE_ID_RE.search(raw)
    if not match:
        return None
    return match.group(1).lower()


def fetch_album_from_musicbrainz_link(
    text: str,
    *,
    discid: str = '',
    preferred_track_count: int = 0,
) -> AlbumMetadata | None:
    """Load one album from a MusicBrainz release URL or UUID.

    Raises:
        ValueError: when the text is not a valid release link/id.
    """
    release_id = parse_musicbrainz_release_id(text)
    if not release_id:
        raise ValueError(
            'Not a MusicBrainz release link. '
            'Use musicbrainz.org/release/… (not release-group).'
        )
    album = MusicBrainzProvider().get_release(
        release_id,
        discid=discid,
        preferred_track_count=preferred_track_count,
    )
    if album is None:
        raise ValueError('Could not load that MusicBrainz release')
    if discid and not album.discid:
        album.discid = discid
    return album


def lookup_metadata(
    musicbrainz_discid: str | None,
    freedb_id: str | None,
    *,
    use_musicbrainz: bool = True,
    use_freedb: bool = True,
    offsets: tuple[int, ...] = (),
    track_count: int = 0,
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
        except Exception:
            log.exception('MusicBrainz lookup failed')

    if use_freedb and freedb_id:
        try:
            _add(
                FreeDBProvider(
                    offsets=offsets,
                    track_count=track_count,
                ).lookup_by_discid(freedb_id)
            )
        except Exception:
            log.exception('FreeDB lookup failed')

    return results
