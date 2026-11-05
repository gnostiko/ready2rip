# SPDX-License-Identifier: GPL-3.0-or-later
"""Output path helpers, filename sanitization, and naming schemes."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ready2rip.metadata.providers import AlbumMetadata, TrackMetadata
from ready2rip.tags.artists import (
    ARTIST_STYLE_FEAT,
    ARTIST_STYLE_SLASH,
    DEFAULT_ARTIST_STYLE,
    format_artists,
    format_title_with_artists,
    normalize_artist_style,
    primary_artist,
)
from ready2rip.util import ensure_path_under

_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_MULTI_SPACE = re.compile(r'\s+')
_WIN_RESERVED = {
    'CON', 'PRN', 'AUX', 'NUL',
    *(f'COM{i}' for i in range(1, 10)),
    *(f'LPT{i}' for i in range(1, 10)),
}

# Track files stay 01/02/…; multi-disc albums use a CD1/ CD2/ subfolder.
DEFAULT_FILENAME_TEMPLATE = '{track:02d} - {title}'
DEFAULT_ALBUM_FOLDER_TEMPLATE = '{album_artist}/{dated_album}/{disc_folder}'

# Tokens shown in settings tooltips / docs.
FOLDER_TEMPLATE_HELP = (
    '{album_artist} {album} {dated_album} {date} {year} {genre} {label} '
    '{disc} {totaldiscs} {disc_folder}'
)
FILENAME_TEMPLATE_HELP = (
    '{track:02d} {disc_track} {disc_prefix} {title} {artist} {album_artist} '
    '{album} {year} {date} {genre} {disc} {totaldiscs} {track_total}'
)


@dataclass(frozen=True)
class NamingScheme:
    """Preset folder or filename template (Custom is last, empty template)."""

    id: str
    label: str
    template: str
    description: str = ''


def scheme_tooltip(scheme: NamingScheme) -> str:
    """Hover text: template (or Custom) plus a short description."""
    head = scheme.template if scheme.template else 'Custom template'
    desc = (scheme.description or '').strip()
    return f'{head}\n{desc}' if desc else head


# Album folder layouts. Presets append {disc_folder} (empty on single-disc).
FOLDER_SCHEMES: tuple[NamingScheme, ...] = (
    NamingScheme(
        'artist_dated',
        'Artist / (Date) - Album',
        DEFAULT_ALBUM_FOLDER_TEMPLATE,
        'Full release date when known; otherwise just the album name.',
    ),
    NamingScheme(
        'artist_album_year',
        'Artist / Album (Year)',
        '{album_artist}/{album} ({year})/{disc_folder}',
        'Common library layout — album artist, then album with year.',
    ),
    NamingScheme(
        'artist_album',
        'Artist / Album',
        '{album_artist}/{album}/{disc_folder}',
        'Album artist and album title only.',
    ),
    NamingScheme(
        'custom',
        'Custom…',
        '',
        'Write your own folder template with the tokens below.',
    ),
)

FILENAME_SCHEMES: tuple[NamingScheme, ...] = (
    NamingScheme(
        'smart_disc',
        '01 - Title',
        DEFAULT_FILENAME_TEMPLATE,
        'Track number and title.',
    ),
    NamingScheme(
        'va_smart',
        '01 - Artist - Title',
        '{track:02d} - {artist} - {title}',
        'Includes the track artist — useful for compilations.',
    ),
    NamingScheme(
        'custom',
        'Custom…',
        '',
        'Write your own filename template with the tokens below.',
    ),
)


def scheme_index(schemes: tuple[NamingScheme, ...], template: str) -> int:
    """Index of the preset matching *template*, or Custom (last) if none."""
    text = (template or '').strip()
    custom = len(schemes) - 1
    for i, scheme in enumerate(schemes):
        if scheme.id == 'custom':
            continue
        if scheme.template == text:
            return i
    return custom


# Prior defaults → current dated-album + optional CD# folder.
_LEGACY_DEFAULT_FOLDER_TEMPLATES = {
    '{album_artist}/({date}) - {album}',
    '{album_artist}/({date}) - {album}/{disc_folder}',
    '{album_artist}/{dated_album}',
    '{album_artist}/{dated_album}/{disc_folder}',
}

# Presets that lost trailing {disc_folder} under the old filename-prefix scheme.
_FOLDER_PRESETS_ADD_DISC = {
    '{album_artist}/{album} ({year})': '{album_artist}/{album} ({year})/{disc_folder}',
    '{album_artist}/{album}': '{album_artist}/{album}/{disc_folder}',
}

_LEGACY_DEFAULT_FILENAME_TEMPLATES = {
    '{track:02d} - {title}',
    '{disc_track} - {title}',
}

_FILENAME_PRESETS_NORMALIZE = {
    '{disc_track} - {artist} - {title}': '{track:02d} - {artist} - {title}',
}


def migrate_album_folder_template(template: str | None) -> str:
    """Normalize stored album-folder templates to the current presets."""
    text = (template or '').strip()
    if not text or text in _LEGACY_DEFAULT_FOLDER_TEMPLATES:
        return DEFAULT_ALBUM_FOLDER_TEMPLATE
    if text in _FOLDER_PRESETS_ADD_DISC:
        return _FOLDER_PRESETS_ADD_DISC[text]
    return text


def migrate_filename_template(template: str | None) -> str:
    """Normalize stored filename templates to the current default when legacy."""
    text = (template or '').strip()
    if not text or text in _LEGACY_DEFAULT_FILENAME_TEMPLATES:
        return DEFAULT_FILENAME_TEMPLATE
    return _FILENAME_PRESETS_NORMALIZE.get(text, text)


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


def template_values(
    *,
    album: AlbumMetadata | None,
    track_number: int,
    artist_style: str | None = DEFAULT_ARTIST_STYLE,
) -> dict[str, object]:
    """Placeholder map shared by folder and filename templates."""
    style = normalize_artist_style(artist_style)
    album_artist = 'Unknown Artist'
    album_title = 'Unknown Album'
    year = ''
    date = ''
    genre = ''
    label = ''
    track_title = f'Track {track_number:02d}'
    track_artist = album_artist

    disc = 1
    total_discs = 1
    track_total = 0
    if album is not None:
        album_artist = album.artist or album_artist
        album_title = album.title or album_title
        genre = album.genre or ''
        label = album.label or ''
        if album.date:
            date = album.date.strip()
            year = date[:4] if len(date) >= 4 else date
        disc = max(1, int(album.medium_position or 1))
        total_discs = max(1, int(album.medium_count or 1))
        track_total = len(album.tracks) if album.tracks else 0
        meta = track_meta_for(album, track_number)
        if meta is not None:
            if meta.title:
                track_title = meta.title
            if meta.artist:
                track_artist = meta.artist
        else:
            track_artist = album_artist

    # Multi-disc → CD1 / CD2 / … subfolder; single-disc → omitted path part.
    disc_folder = f'CD{disc}' if total_discs > 1 else ''
    # Filenames always use per-disc track numbers (01, 02, …).
    track_padded = f'{track_number:02d}'
    disc_prefix = ''
    disc_track = track_padded

    if style == ARTIST_STYLE_FEAT:
        # Title carries guests; keep folder/{artist} as the primary performer.
        display_album_artist = primary_artist(album_artist) or album_artist
        display_track_artist = primary_artist(track_artist) or track_artist
        display_title = format_title_with_artists(track_title, track_artist, style)
    else:
        display_album_artist = format_artists(album_artist, style)
        display_track_artist = format_artists(track_artist, style)
        display_title = track_title

    safe_artist = sanitize_component(
        _path_safe_artist(display_album_artist, style)
    )
    safe_album = sanitize_component(album_title)
    return {
        'album_artist': safe_artist,
        'albumartist': safe_artist,  # Alias of album_artist
        'album': safe_album,
        'year': sanitize_component(year, fallback=''),
        'date': sanitize_component(date, fallback=''),
        # (YYYY-MM-DD) - Album when a date is known; otherwise just the album.
        'dated_album': sanitize_component(
            f'({date}) - {album_title}' if date else album_title
        ),
        'genre': sanitize_component(genre or 'Unknown Genre'),
        'label': sanitize_component(label, fallback='') if label else '',
        'track': track_number,
        'track_total': track_total,
        'title': sanitize_component(display_title),
        'artist': sanitize_component(
            _path_safe_artist(display_track_artist, style)
        ),
        'disc': disc,
        'totaldiscs': total_discs,
        'disc_prefix': disc_prefix,
        'disc_track': disc_track,
        'disc_folder': sanitize_component(disc_folder, fallback='') if disc_folder else '',
    }


def _path_safe_artist(value: str, style: str) -> str:
    """Keep slash-style artists readable in paths (ASCII ``/`` is unsafe)."""
    if normalize_artist_style(style) != ARTIST_STYLE_SLASH:
        return value
    # Fullwidth solidus looks like ``/`` but is a valid filename character.
    return value.replace('/', '／')


def build_output_paths(
    *,
    base_dir: Path,
    album_folder_template: str,
    filename_template: str,
    album: AlbumMetadata | None,
    track_number: int,
    extension: str,
    artist_style: str | None = DEFAULT_ARTIST_STYLE,
) -> tuple[Path, Path]:
    """Return ``(album_dir, file_path)`` for one track."""
    values = template_values(
        album=album,
        track_number=track_number,
        artist_style=artist_style,
    )

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
