# SPDX-License-Identifier: GPL-3.0-or-later
"""CUE sheet generation for continuous disc images and multi-file rips."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from ready2rip.disc.probe import DataTrackInfo, DiscInfo, TrackInfo
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
    # PREGAP command (silence not stored in the file), typically track-1 pause.
    pregap_sectors: int = 0
    is_data: bool = False
    data_mode: str = 'MODE1/2352'


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
    track-1 boundary — HTOA layout in a single image.

    Inter-track INDEX 00 is placed at ``start - pregap`` when that sector is
    inside the image (cyanrip/EAC noncompliant: gaps merged into previous).
    A standard track-1 pause that is not in the file is emitted as PREGAP.
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
        index00: int | None = None
        pregap_cmd = 0
        if (
            htoa_index00
            and t.number == 1
            and t.start_sector > file_start_sector
        ):
            index00 = file_start_sector
        elif t.number == 1 and not htoa_index00 and t.pregap_sectors > 0:
            pregap_cmd = t.pregap_sectors
        elif t.number > 1 and t.pregap_sectors > 0:
            abs00 = t.start_sector - t.pregap_sectors
            if abs00 >= file_start_sector:
                index00 = abs00
        tracks.append(
            CueTrack(
                number=t.number,
                title=title,
                performer=performer,
                index01_sector=t.start_sector,
                index00_sector=index00,
                pregap_sectors=pregap_cmd,
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
    data_files: Sequence[tuple[int, Path | str]] | None = None,
) -> None:
    """Write a CUE sheet for a continuous *image_filename*.

    *file_type* is the CUE FILE type token: ``WAVE``, ``FLAC``, etc.
    Offsets in the sheet are relative to the start of the audio file.
    Optional *data_files* are raw 2352-byte ``.bin`` files (TOC order).
    """
    lines = _cue_header(
        album,
        comment=_image_comment(disc, data_files),
    )
    audio_tracks = build_cue_tracks(
        disc, album, file_start_sector=file_start_sector, htoa_index00=htoa_index00
    )
    data_by_num = _data_file_map(disc, data_files)
    numbers = _ordered_numbers(audio_tracks, data_by_num)
    audio_emitted = False
    audio_by_num = {ct.number: ct for ct in audio_tracks}

    for number in numbers:
        data = data_by_num.get(number)
        if data is not None:
            path, info = data
            lines.append(f'FILE {_cue_quote(path.name)} BINARY')
            lines.append(f'  TRACK {number:02d} {info.cue_mode}')
            if info.pregap_sectors > 0:
                lines.append(f'    PREGAP {sectors_to_msf(info.pregap_sectors)}')
            lines.append(f'    INDEX 01 {sectors_to_msf(0)}')
            continue

        ct = audio_by_num.get(number)
        if ct is None:
            continue
        if not audio_emitted:
            lines.append(f'FILE {_cue_quote(image_filename)} {file_type}')
            audio_emitted = True
        lines.append(f'  TRACK {ct.number:02d} AUDIO')
        lines.append(f'    TITLE {_cue_quote(ct.title)}')
        lines.append(f'    PERFORMER {_cue_quote(ct.performer)}')
        if ct.pregap_sectors > 0:
            lines.append(f'    PREGAP {sectors_to_msf(ct.pregap_sectors)}')
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
    track_files: Sequence[tuple[int, Path | str]],
    file_type: str,
    disc: DiscInfo | None,
    album: AlbumMetadata | None,
    htoa_file: Path | str | None = None,
    data_files: Sequence[tuple[int, Path | str]] | None = None,
) -> None:
    """Write a multi-file CUE (gaps appended to the previous track).

    Each audio track is its own ``FILE`` with ``INDEX 01 00:00:00``. cdparanoia
    already extracts INDEX 01 → next INDEX 01, so the next track's INDEX 00
    lives at the tail of the previous file (EAC/cyanrip noncompliant). A
    standard 2s track-1 pause that is not stored is ``PREGAP``. Optional
    *htoa_file* is linked as track 01 ``INDEX 00``. Data tracks are raw
    BINARY files in TOC order.
    """
    lines = _cue_header(
        album,
        comment=_multi_comment(disc, data_files),
    )

    album_artist = (album.artist if album else '') or 'Unknown Artist'
    ordered = sorted(
        ((int(n), Path(p)) for n, p in track_files if int(n) > 0),
        key=lambda item: item[0],
    )
    data_by_num = _data_file_map(disc, data_files)
    if not ordered and htoa_file is None and not data_by_num:
        raise ValueError('No track files for multi-file CUE sheet')

    audio_by_num = {n: p for n, p in ordered}
    track_info = {t.number: t for t in (disc.tracks if disc else [])}
    numbers = _ordered_numbers_from_files(ordered, data_by_num, disc)

    prev_audio: TrackInfo | None = None
    first_audio = True

    def emit_data(number: int) -> None:
        item = data_by_num.get(number)
        if item is None:
            return
        path, info = item
        lines.append(f'FILE {_cue_quote(path.name)} BINARY')
        lines.append(f'  TRACK {number:02d} {info.cue_mode}')
        if info.pregap_sectors > 0:
            lines.append(f'    PREGAP {sectors_to_msf(info.pregap_sectors)}')
        lines.append(f'    INDEX 01 {sectors_to_msf(0)}')

    for number in numbers:
        if number in data_by_num:
            emit_data(number)
            continue
        path = audio_by_num.get(number)
        if path is None:
            continue
        meta = track_meta_for(album, number)
        title = (meta.title if meta and meta.title else '') or f'Track {number:02d}'
        performer = (
            (meta.artist if meta and meta.artist else '') or album_artist
        )
        tinfo = track_info.get(number)
        fname = path.name

        if first_audio and htoa_file is not None:
            htoa_name = Path(htoa_file).name
            lines.append(f'FILE {_cue_quote(htoa_name)} {file_type}')
            lines.append(f'  TRACK {number:02d} AUDIO')
            lines.append(f'    TITLE {_cue_quote(title)}')
            lines.append(f'    PERFORMER {_cue_quote(performer)}')
            lines.append(f'    INDEX 00 {sectors_to_msf(0)}')
            lines.append(f'FILE {_cue_quote(fname)} {file_type}')
            lines.append(f'    INDEX 01 {sectors_to_msf(0)}')
            first_audio = False
            prev_audio = tinfo
            continue

        pregap = tinfo.pregap_sectors if tinfo is not None else 0
        if (
            tinfo is not None
            and pregap > 0
            and prev_audio is not None
            and number > 1
        ):
            rel = prev_audio.length_sectors - pregap
            if rel < 0:
                rel = 0
            lines.append(f'  TRACK {number:02d} AUDIO')
            lines.append(f'    TITLE {_cue_quote(title)}')
            lines.append(f'    PERFORMER {_cue_quote(performer)}')
            lines.append(f'    INDEX 00 {sectors_to_msf(rel)}')
            lines.append(f'FILE {_cue_quote(fname)} {file_type}')
            lines.append(f'    INDEX 01 {sectors_to_msf(0)}')
        else:
            lines.append(f'FILE {_cue_quote(fname)} {file_type}')
            lines.append(f'  TRACK {number:02d} AUDIO')
            lines.append(f'    TITLE {_cue_quote(title)}')
            lines.append(f'    PERFORMER {_cue_quote(performer)}')
            if (
                tinfo is not None
                and tinfo.number == 1
                and htoa_file is None
                and pregap > 0
            ):
                lines.append(f'    PREGAP {sectors_to_msf(pregap)}')
            elif tinfo is not None and pregap > 0 and prev_audio is None:
                # Pregap not stored in a previous ripped file.
                lines.append(f'    PREGAP {sectors_to_msf(pregap)}')
            lines.append(f'    INDEX 01 {sectors_to_msf(0)}')

        first_audio = False
        prev_audio = tinfo

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
    from ready2rip.util import normalize_release_date

    date = normalize_release_date((album.date if album else '') or '')

    lines: list[str] = []
    if date:
        # Full YYYY-MM-DD when known (year-only still valid).
        lines.append(f'REM DATE {date}')
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
    if e == 'mp3':
        return 'MP3'
    # AAC/M4A and other lossy containers have no standard CUE FILE type.
    return 'WAVE'


def _data_file_map(
    disc: DiscInfo | None,
    data_files: Sequence[tuple[int, Path | str]] | None,
) -> dict[int, tuple[Path, DataTrackInfo]]:
    if not data_files:
        return {}
    info_by_num = {d.number: d for d in (disc.data_tracks if disc else [])}
    out: dict[int, tuple[Path, DataTrackInfo]] = {}
    for number, path in data_files:
        n = int(number)
        info = info_by_num.get(n) or DataTrackInfo(
            number=n,
            start_sector=0,
            length_sectors=0,
            mode=1,
        )
        out[n] = (Path(path), info)
    return out


def _ordered_numbers(
    audio_tracks: Sequence[CueTrack],
    data_by_num: dict[int, tuple[Path, DataTrackInfo]],
) -> list[int]:
    nums = {ct.number for ct in audio_tracks} | set(data_by_num)
    return sorted(nums)


def _ordered_numbers_from_files(
    audio: Sequence[tuple[int, Path]],
    data_by_num: dict[int, tuple[Path, DataTrackInfo]],
    disc: DiscInfo | None,
) -> list[int]:
    nums = {n for n, _p in audio} | set(data_by_num)
    if disc is not None:
        nums |= {t.number for t in disc.tracks if t.number in {n for n, _p in audio}}
    return sorted(nums)


def _image_comment(
    disc: DiscInfo,
    data_files: Sequence[tuple[int, Path | str]] | None,
) -> str:
    bits = ['ready2rip — Copy Image']
    if any(t.pregap_sectors for t in disc.tracks):
        bits.append('INDEX 00 merged into previous')
    if data_files:
        bits.append('mixed-mode data')
    return '; '.join(bits)


def _multi_comment(
    disc: DiscInfo | None,
    data_files: Sequence[tuple[int, Path | str]] | None,
) -> str:
    has_gaps = bool(disc and any(t.pregap_sectors for t in disc.tracks))
    if has_gaps:
        comment = (
            'ready2rip — Multiple files with gaps appended to previous '
            '(EAC default / noncompliant)'
        )
    else:
        comment = 'ready2rip — Multiple files with left-out gaps (secure rip)'
    if data_files:
        comment += '; mixed-mode data'
    return comment


def _cue_quote(value: str) -> str:
    cleaned = (value or '').replace('"', "'")
    return f'"{cleaned}"'
