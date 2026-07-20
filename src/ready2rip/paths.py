# SPDX-License-Identifier: GPL-3.0-or-later
"""Output path helpers and filename sanitization."""

from __future__ import annotations

import re
from pathlib import Path

from ready2rip.metadata.providers import AlbumMetadata, TrackMetadata
from ready2rip.util import ensure_path_under

_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_MULTI_SPACE = re.compile(r'\s+')
_WIN_RESERVED = {
    'CON', 'PRN', 'AUX', 'NUL',
    *(f'COM{i}' for i in range(1, 10)),
    *(f'LPT{i}' for i in range(1, 10)),
}


def sanitize_component(name: str, *, fallback: str = 'Unknown') -> str:
    """Make a single path component safe for common filesystems."""
    cleaned = _UNSAFE.sub('', name).strip().strip('.')
    cleaned = _MULTI_SPACE.sub(' ', cleaned)
    # Avoid empty or Windows-reserved names on shared disks.
    if not cleaned or cleaned in {'.', '..'}:
        return fallback
    if cleaned.upper() in _WIN_RESERVED:
        cleaned = f'_{cleaned}'
    return cleaned[:180]


def format_template(template: str, values: dict) -> str:
    """Format *template* with ``str.format_map``, falling back on KeyError."""
    class _Safe(dict):
        def __missing__(self, key: str) -> str:
            return '{' + key + '}'

    try:
        return template.format_map(_Safe(values))
    except (ValueError, IndexError):
        return template


def track_meta_for(album: AlbumMetadata | None, number: int) -> TrackMetadata | None:
    if album is None:
        return None
    for track in album.tracks:
        if track.number == number:
            return track
    idx = number - 1
    if 0 <= idx < len(album.tracks):
        return album.tracks[idx]
    return None


def build_output_paths(
    *,
    base_dir: Path,
    album_folder_template: str,
    filename_template: str,
    album: AlbumMetadata | None,
    track_number: int,
    extension: str,
) -> tuple[Path, Path]:
    """Return ``(album_dir, file_path)`` for one track."""
    album_artist = 'Unknown Artist'
    album_title = 'Unknown Album'
    year = ''
    track_title = f'Track {track_number:02d}'
    track_artist = album_artist

    disc = 1
    total_discs = 1
    if album is not None:
        album_artist = album.artist or album_artist
        album_title = album.title or album_title
        if album.date:
            year = album.date[:4] if len(album.date) >= 4 else album.date
        disc = max(1, int(album.medium_position or 1))
        total_discs = max(1, int(album.medium_count or 1))
        meta = track_meta_for(album, track_number)
        if meta is not None:
            if meta.title:
                track_title = meta.title
            if meta.artist:
                track_artist = meta.artist
        else:
            track_artist = album_artist

    # Subfolder only when multi-disc (avoids CD1 noise for single discs).
    disc_folder = f'CD{disc}' if total_discs > 1 else ''

    values = {
        'album_artist': sanitize_component(album_artist),
        'album': sanitize_component(album_title),
        'year': sanitize_component(year, fallback=''),
        'track': track_number,
        'title': sanitize_component(track_title),
        'artist': sanitize_component(track_artist),
        'disc': disc,
        'totaldiscs': total_discs,
        'disc_folder': sanitize_component(disc_folder, fallback='') if disc_folder else '',
    }

    folder_rel = format_template(album_folder_template, values)
    # Split folder template into path parts and sanitize each.
    # Never honor absolute templates — always stay under base_dir.
    parts = []
    for p in Path(folder_rel).parts:
        if p in ('', '.', '..', '/'):
            continue
        cleaned = sanitize_component(p, fallback='')
        if cleaned:
            parts.append(cleaned)
    base = base_dir.expanduser()
    album_dir = base.joinpath(*parts) if parts else base
    # Defense in depth: refuse path escape if templates ever misbehave.
    album_dir = ensure_path_under(base, album_dir)

    file_stem = format_template(filename_template, values)
    file_stem = sanitize_component(file_stem, fallback=f'{track_number:02d}')
    if not extension.startswith('.'):
        extension = f'.{extension}'
    file_path = album_dir / f'{file_stem}{extension}'
    ensure_path_under(base, file_path)
    return album_dir, file_path
