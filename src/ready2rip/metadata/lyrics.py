# SPDX-License-Identifier: GPL-3.0-or-later
"""Fetch unsynced / synced lyrics from LRCLIB (https://lrclib.net)."""

from __future__ import annotations

import json
import logging
import random
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ready2rip.config import APPLICATION_NAME, APPLICATION_VERSION
from ready2rip.disc.probe import DiscInfo
from ready2rip.metadata.providers import AlbumMetadata, TrackMetadata
from ready2rip.util import drain_http_error, is_safe_http_url, read_limited

log = logging.getLogger(__name__)

LRCLIB_BASE = 'https://lrclib.net'
USER_AGENT = (
    f'{APPLICATION_NAME}/{APPLICATION_VERSION} '
    f'(https://github.com/gnostiko/ready2rip)'
)

# Stay under the identified-client ~90/min allowance for batch album fetches.
_MIN_INTERVAL = 0.35
_LOCK = threading.Lock()
_LAST_REQUEST = 0.0

_LRC_LINE = re.compile(
    r'^\[(\d{1,2}):(\d{2})(?:[.:](\d{1,3}))?\]\s*(.*)$'
)


@dataclass(frozen=True)
class LyricsResult:
    plain: str = ''
    synced: str = ''
    instrumental: bool = False
    source: str = 'lrclib'


def plain_from_lrc(synced: str) -> str:
    """Strip LRC timestamps to plain text (best-effort)."""
    lines: list[str] = []
    for raw in (synced or '').splitlines():
        text = raw.strip()
        if not text:
            if lines and lines[-1] != '':
                lines.append('')
            continue
        match = _LRC_LINE.match(text)
        if match:
            body = (match.group(4) or '').strip()
            if body:
                lines.append(body)
        elif not text.startswith('['):
            lines.append(text)
    # Collapse trailing blanks.
    while lines and not lines[-1]:
        lines.pop()
    return '\n'.join(lines).strip()


def embed_lyrics_text(track: TrackMetadata | None) -> str:
    """Plain lyrics suitable for USLT / ©lyr / UNSYNCEDLYRICS."""
    if track is None:
        return ''
    plain = (track.lyrics or '').strip()
    if plain:
        return plain
    synced = (track.lyrics_synced or '').strip()
    if synced:
        return plain_from_lrc(synced)
    return ''


def write_lyrics_sidecar(out_path: Path, track: TrackMetadata | None) -> Path | None:
    """Write ``.lrc`` (synced) or ``.txt`` (plain) next to a lossless track file."""
    if track is None or out_path is None:
        return None
    synced = (track.lyrics_synced or '').strip()
    plain = embed_lyrics_text(track)
    if not synced and not plain:
        return None
    try:
        if synced:
            path = out_path.with_suffix('.lrc')
            path.write_text(synced + ('' if synced.endswith('\n') else '\n'), encoding='utf-8')
        else:
            path = out_path.with_suffix('.txt')
            path.write_text(plain + ('' if plain.endswith('\n') else '\n'), encoding='utf-8')
        return path
    except OSError as exc:
        log.warning('Could not write lyrics sidecar for %s: %s', out_path, exc)
        return None


def sectors_to_duration_seconds(sectors: int) -> float:
    return max(0.0, float(sectors) / 75.0)


def duration_for_track(disc: DiscInfo | None, number: int) -> float | None:
    if disc is None:
        return None
    for track in disc.tracks:
        if track.number == number:
            return sectors_to_duration_seconds(track.length_sectors)
    return None


class LrclibClient:
    """Sequential LRCLIB client with User-Agent identity and 429 backoff."""

    def __init__(
        self,
        *,
        user_agent: str = USER_AGENT,
        timeout: float = 20.0,
        min_interval: float = _MIN_INTERVAL,
    ) -> None:
        self.user_agent = user_agent
        self.timeout = timeout
        self.min_interval = min_interval

    def fetch_for_track(
        self,
        *,
        artist: str,
        title: str,
        album: str = '',
        duration: float | None = None,
    ) -> LyricsResult | None:
        artist = (artist or '').strip()
        title = (title or '').strip()
        album = (album or '').strip()
        if not artist or not title:
            return None

        got = self._get(
            artist_name=artist,
            track_name=title,
            album_name=album,
            duration=duration,
        )
        if got is not None:
            return got

        hits = self._search(
            artist_name=artist,
            track_name=title,
            album_name=album,
        )
        return _best_search_hit(hits, album=album, duration=duration)

    def fetch_album(
        self,
        album: AlbumMetadata,
        disc: DiscInfo | None = None,
        *,
        only_missing: bool = True,
    ) -> int:
        """Fill lyrics on *album.tracks* in place. Returns tracks updated."""
        updated = 0
        album_title = (album.title or '').strip()
        album_artist = (album.artist or '').strip()
        for track in album.tracks:
            if only_missing and (
                (track.lyrics or '').strip() or (track.lyrics_synced or '').strip()
            ):
                continue
            artist = (track.artist or album_artist or '').strip()
            title = (track.title or '').strip()
            if not artist or not title:
                continue
            duration = duration_for_track(disc, track.number)
            try:
                result = self.fetch_for_track(
                    artist=artist,
                    title=title,
                    album=album_title,
                    duration=duration,
                )
            except _RateLimitFatal as exc:
                log.warning('LRCLIB rate limited; stopping album fetch: %s', exc)
                break
            except Exception:
                log.debug('LRCLIB fetch failed for track %s', track.number, exc_info=True)
                continue
            if result is None:
                continue
            if result.instrumental and not result.plain and not result.synced:
                track.lyrics = ''
                track.lyrics_synced = ''
                track.lyrics_source = 'lrclib-instrumental'
                updated += 1
                continue
            if result.plain:
                track.lyrics = result.plain
            elif result.synced:
                track.lyrics = plain_from_lrc(result.synced)
            if result.synced:
                track.lyrics_synced = result.synced
            track.lyrics_source = result.source
            updated += 1
        return updated

    def _get(
        self,
        *,
        artist_name: str,
        track_name: str,
        album_name: str,
        duration: float | None,
    ) -> LyricsResult | None:
        params: dict[str, str] = {
            'artist_name': artist_name,
            'track_name': track_name,
        }
        if album_name:
            params['album_name'] = album_name
        if duration is not None and duration > 0:
            # LRCLIB expects seconds; keep one decimal for ±2s matching.
            params['duration'] = f'{duration:.3f}'.rstrip('0').rstrip('.')
        url = f'{LRCLIB_BASE}/api/get?{urllib.parse.urlencode(params)}'
        payload = self._request_json(url, allow_404=True)
        if not isinstance(payload, dict):
            return None
        return _result_from_record(payload)

    def _search(
        self,
        *,
        artist_name: str,
        track_name: str,
        album_name: str,
    ) -> list[dict[str, Any]]:
        params: dict[str, str] = {
            'artist_name': artist_name,
            'track_name': track_name,
        }
        if album_name:
            params['album_name'] = album_name
        url = f'{LRCLIB_BASE}/api/search?{urllib.parse.urlencode(params)}'
        payload = self._request_json(url, allow_404=True)
        if isinstance(payload, list):
            return [row for row in payload if isinstance(row, dict)]
        return []

    def _throttle(self) -> None:
        global _LAST_REQUEST
        with _LOCK:
            now = time.monotonic()
            wait = self.min_interval - (now - _LAST_REQUEST)
            if wait > 0:
                time.sleep(wait)
            _LAST_REQUEST = time.monotonic()

    def _request_json(
        self, url: str, *, allow_404: bool = False, attempt: int = 0
    ) -> Any | None:
        if not is_safe_http_url(url):
            log.warning('Refusing non-allowlisted lyrics URL: %s', url)
            return None
        self._throttle()
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
                    log.warning('Refusing lyrics redirect to unsafe URL: %s', final)
                    return None
                body = read_limited(response).decode('utf-8', errors='replace')
                if not body.strip():
                    return None
                return json.loads(body)
        except urllib.error.HTTPError as exc:
            code = int(exc.code)
            if code == 404 and allow_404:
                drain_http_error(exc)
                return None
            if code in {429, 503} and attempt < 2:
                retry_after = _retry_after_seconds(exc)
                drain_http_error(exc)
                delay = retry_after * (1.0 + random.uniform(0.0, 0.3))
                log.info('LRCLIB HTTP %s — waiting %.1fs', code, delay)
                time.sleep(delay)
                if code == 429 and attempt >= 1:
                    raise _RateLimitFatal(f'HTTP {code} after retry') from None
                return self._request_json(
                    url, allow_404=allow_404, attempt=attempt + 1
                )
            log.debug('LRCLIB HTTP %s for %s', code, url)
            drain_http_error(exc)
            return None
        except (
            urllib.error.URLError,
            TimeoutError,
            json.JSONDecodeError,
            OSError,
            ValueError,
        ) as exc:
            log.warning('LRCLIB request failed (%s): %s', url, exc)
            return None


class _RateLimitFatal(RuntimeError):
    """Stop an album batch after repeated 429s."""


def _retry_after_seconds(exc: urllib.error.HTTPError) -> float:
    raw = ''
    try:
        raw = exc.headers.get('Retry-After') or ''
    except Exception:
        raw = ''
    try:
        return max(1.0, float(raw))
    except (TypeError, ValueError):
        return 2.0


def _result_from_record(row: dict[str, Any]) -> LyricsResult | None:
    instrumental = bool(row.get('instrumental'))
    plain = row.get('plainLyrics')
    synced = row.get('syncedLyrics')
    plain_s = plain.strip() if isinstance(plain, str) else ''
    synced_s = synced.strip() if isinstance(synced, str) else ''
    if not plain_s and not synced_s and not instrumental:
        return None
    return LyricsResult(
        plain=plain_s,
        synced=synced_s,
        instrumental=instrumental,
        source='lrclib',
    )


def _best_search_hit(
    hits: list[dict[str, Any]],
    *,
    album: str,
    duration: float | None,
) -> LyricsResult | None:
    if not hits:
        return None
    album_cf = album.casefold()
    scored: list[tuple[tuple, dict[str, Any]]] = []
    for row in hits:
        dur = row.get('duration')
        try:
            dur_f = float(dur) if dur is not None else None
        except (TypeError, ValueError):
            dur_f = None
        dur_score = 0
        if duration is not None and dur_f is not None:
            delta = abs(dur_f - duration)
            if delta <= 2.0:
                dur_score = 2
            elif delta <= 5.0:
                dur_score = 1
        album_hit = 0
        name = row.get('albumName')
        if album_cf and isinstance(name, str) and name.casefold() == album_cf:
            album_hit = 1
        has_text = 1 if (row.get('plainLyrics') or row.get('syncedLyrics')) else 0
        scored.append(((dur_score, album_hit, has_text), row))
    scored.sort(key=lambda item: item[0], reverse=True)
    best = scored[0]
    # Reject poor duration mismatches when we know duration.
    if duration is not None and best[0][0] == 0:
        # Still allow if nothing has a duration score.
        if any(item[0][0] > 0 for item in scored):
            return None
    return _result_from_record(best[1])
