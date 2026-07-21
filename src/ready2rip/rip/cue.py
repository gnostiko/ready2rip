# SPDX-License-Identifier: GPL-3.0-or-later
"""EAC-style CUE sheet generation for continuous disc images."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ready2rip.disc.probe import DiscInfo
from ready2rip.metadata.providers import AlbumMetadata
from ready2rip.paths import sanitize_component, track_meta_for


@dataclass(frozen=True)
class CueTrack:
    """One TRACK entry in a CUE sheet."""

    number: int
    title: str
    performer: str
    # Absolute sector on disc where INDEX 01 starts
    index01_sector: int
    # Absolute sector for INDEX 00 if present (HTOA / pregap into previous)
    index00_sector: int | None = None


def sectors_to_msf(sectors: int) -> str:
    """Convert CDDA sector count to ``MM:SS:FF`` (75 frames per second)."""
    if sectors < 0:
        sectors = 0
    frames = int(sectors)
    minutes, rem = divmod(frames, 75 * 60)
    seconds, ff = divmod(rem, 75)
    return f'{minutes:02d}:{seconds:02d}:{ff:02d}'


def build_cue_tracks(
    disc: DiscInfo,
    album: AlbumMetadata | None,
    *,
    file_start_sector: int,
    htoa_index00: bool = False,
) -> list[CueTrack]:
    """Build cue track list relative to an image that starts at *file_start_sector*.

    When *htoa_index00* is True and track 1 starts after *file_start_sector*,
    TRACK 01 gets INDEX 00 at the file start (00:00:00) and INDEX 01 at the
    track-1 boundary — classic EAC HTOA layout in a single image.
    """
    album_artist = (album.artist if album else '') or 'Unknown Artist'
    tracks: list[CueTrack] = []
    for t in disc.tracks:
        meta = track_meta_for(album, t.number)
        title = (meta.title if meta and meta.title else '') or f'Track {t.number:02d}'
        performer = (
            (meta.artist if meta and meta.artist else '')
            or album_artist
        )
        index00 = None
        if (
            htoa_index00
            and t.number == 1
            and t.start_sector > file_start_sector
        ):
            index00 = file_start_sector
        tracks.append(
            CueTrack(
                number=t.number,
                title=title,
                performer=performer,
                index01_sector=t.start_sector,
                index00_sector=index00,
            )
        )
    return tracks


def write_cue_sheet(
    cue_path: Path,
    *,
    image_filename: str,
    file_type: str,
    disc: DiscInfo,
    album: AlbumMetadata | None,
    file_start_sector: int,
    htoa_index00: bool = False,
) -> None:
    """Write an EAC-compatible CUE sheet for a continuous *image_filename*.

    *file_type* is the CUE FILE type token: ``WAVE``, ``FLAC``, etc.
    Offsets in the sheet are relative to the start of the audio file.
    """
    lines = _cue_header(
        album,
        comment='ready2rip — Copy Image (EAC-style)',
    )
    lines.append(f'FILE {_cue_quote(image_filename)} {file_type}')

    cue_tracks = build_cue_tracks(
        disc, album, file_start_sector=file_start_sector, htoa_index00=htoa_index00
    )
    for ct in cue_tracks:
        lines.append(f'  TRACK {ct.number:02d} AUDIO')
        lines.append(f'    TITLE {_cue_quote(ct.title)}')
        lines.append(f'    PERFORMER {_cue_quote(ct.performer)}')
        if ct.index00_sector is not None:
            rel0 = ct.index00_sector - file_start_sector
            lines.append(f'    INDEX 00 {sectors_to_msf(rel0)}')
        rel1 = ct.index01_sector - file_start_sector
        if rel1 < 0:
            rel1 = 0
        lines.append(f'    INDEX 01 {sectors_to_msf(rel1)}')

    cue_path.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def write_multi_file_cue_sheet(
    cue_path: Path,
    *,
    track_files: list[tuple[int, Path | str]],
    file_type: str,
    disc: DiscInfo | None,
    album: AlbumMetadata | None,
    htoa_file: Path | str | None = None,
) -> None:
    """Write an EAC multi-file CUE (“Multiple files with left-out gaps”).

    Each audio track is its own ``FILE`` with ``INDEX 01 00:00:00``. Pregaps
    between tracks are not stored in the files (same as per-track cdparanoia
    extraction from INDEX 01). Optional *htoa_file* is linked as track 01
    ``INDEX 00`` (EAC HTOA layout).
    """
    lines = _cue_header(
        album,
        comment=(
            'ready2rip — Multiple files with left-out gaps '
            '(EAC-style secure rip)'
        ),
    )

    album_artist = (album.artist if album else '') or 'Unknown Artist'
    ordered = sorted(
        ((int(n), Path(p)) for n, p in track_files if int(n) > 0),
        key=lambda item: item[0],
    )
    if not ordered and htoa_file is None:
        raise ValueError('No track files for multi-file CUE sheet')

    first = True
    for number, path in ordered:
        meta = track_meta_for(album, number)
        title = (meta.title if meta and meta.title else '') or f'Track {number:02d}'
        performer = (
            (meta.artist if meta and meta.artist else '') or album_artist
        )
        fname = path.name

        if first and htoa_file is not None:
            # EAC: HTOA file carries TRACK 01 INDEX 00; next file is INDEX 01.
            htoa_name = Path(htoa_file).name
            lines.append(f'FILE {_cue_quote(htoa_name)} {file_type}')
            lines.append(f'  TRACK {number:02d} AUDIO')
            lines.append(f'    TITLE {_cue_quote(title)}')
            lines.append(f'    PERFORMER {_cue_quote(performer)}')
            lines.append(f'    INDEX 00 {sectors_to_msf(0)}')
            lines.append(f'FILE {_cue_quote(fname)} {file_type}')
            lines.append(f'    INDEX 01 {sectors_to_msf(0)}')
            first = False
            continue

        lines.append(f'FILE {_cue_quote(fname)} {file_type}')
        lines.append(f'  TRACK {number:02d} AUDIO')
        lines.append(f'    TITLE {_cue_quote(title)}')
        lines.append(f'    PERFORMER {_cue_quote(performer)}')
        lines.append(f'    INDEX 01 {sectors_to_msf(0)}')
        first = False

    # disc reserved for future pregap INDEX 00 between files
    _ = disc
    cue_path.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def multi_file_cue_basename(album: AlbumMetadata | None, disc: DiscInfo | None) -> str:
    """Base name for a multi-file album ``.cue`` (no extension)."""
    if album and album.title:
        artist = sanitize_component(album.artist or 'Unknown Artist')
        title = sanitize_component(album.title)
        return f'{artist} - {title}'
    if disc is not None:
        return sanitize_component(f'CD ({disc.track_count} tracks)')
    return 'CD'


def _cue_header(album: AlbumMetadata | None, *, comment: str) -> list[str]:
    album_title = (album.title if album else '') or 'Unknown Album'
    album_artist = (album.artist if album else '') or 'Unknown Artist'
    date = (album.date if album else '') or ''

    lines: list[str] = []
    if date:
        year = date[:4] if len(date) >= 4 else date
        lines.append(f'REM DATE {year}')
    lines.append(f'REM COMMENT {_cue_quote(comment)}')
    lines.append(f'PERFORMER {_cue_quote(album_artist)}')
    lines.append(f'TITLE {_cue_quote(album_title)}')
    return lines


def image_basename(album: AlbumMetadata | None, disc: DiscInfo) -> str:
    """Sanitized base name for the image / cue pair (no extension)."""
    if album and album.title:
        artist = sanitize_component(album.artist or 'Unknown Artist')
        title = sanitize_component(album.title)
        return f'{artist} - {title}'
    return sanitize_component(f'CD Image ({disc.track_count} tracks)')


def cue_file_type_for_extension(ext: str) -> str:
    e = ext.lower().lstrip('.')
    if e in ('wav', 'wave'):
        return 'WAVE'
    if e == 'flac':
        return 'FLAC'
    if e in ('aiff', 'aif'):
        return 'AIFF'
    # Lossy formats are uncommon for CD images; still emit a type token.
    return 'WAVE'


def _cue_quote(value: str) -> str:
    cleaned = (value or '').replace('"', "'")
    return f'"{cleaned}"'


def _cue_escape(value: str) -> str:
    return (value or '').replace('\n', ' ').strip()
