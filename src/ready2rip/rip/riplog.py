# SPDX-License-Identifier: GPL-3.0-or-later
"""EAC-style detailed rip status log."""

from __future__ import annotations

import platform
import wave
import zlib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ready2rip import config
from ready2rip.accuraterip import AccurateRipConfidence, AccurateRipResult
from ready2rip.disc.probe import DiscInfo
from ready2rip.metadata.providers import AlbumMetadata
from ready2rip.paths import sanitize_component, track_meta_for


@dataclass
class TrackLogEntry:
    number: int
    title: str = ''
    filename: str = ''
    output_path: Path | None = None
    length_label: str = ''
    start_sector: int = 0
    length_sectors: int = 0
    extract_mode: str = 'secure'  # secure | burst | *+test&copy
    extract_seconds: float | None = None
    extract_speed_x: float | None = None
    peak_percent: float | None = None
    # EAC-style quality 0–100 from paranoia fixups / skips.
    quality_percent: float | None = None
    copy_crc: str = ''
    test_crc: str = ''
    wav_bytes: int = 0
    accuraterip: AccurateRipResult | None = None
    status: str = 'OK'
    notes: list[str] = field(default_factory=list)
    # Pre-rendered error-correction lines (quality, counters, suspicious MSF).
    error_correction_lines: list[str] = field(default_factory=list)
    had_errors: bool = False


class RipLog:
    """Builds a human-readable extraction log similar to Exact Audio Copy."""

    def __init__(self) -> None:
        self.started_at = datetime.now()
        self.finished_at: datetime | None = None
        self.device: str = ''
        self.sample_offset: int = 0
        self.encode_format: str = 'flac'
        self.flac_compression: int = 5
        self.mp3_bitrate: int = 320
        self.opus_bitrate: int = 160
        self.burst_fallback: bool = True
        self.verify_accuraterip: bool = True
        self.apply_replaygain: bool = True
        self.embed_artwork: bool = True
        self.test_and_copy: bool = True
        self.write_cue_file: bool = True
        self.defeat_audio_cache: bool = True
        self.rip_htoa: bool = True
        self.album: AlbumMetadata | None = None
        self.disc: DiscInfo | None = None
        self.ar_status: str = ''
        self.cache_message: str = ''
        self.drive_caches: bool | None = None
        self.accurate_stream: bool | None = None
        self.accurate_stream_message: str = ''
        self.c2_pointers: bool | None = None
        self.c2_message: str = ''
        self.tracks: list[TrackLogEntry] = []
        self.notes: list[str] = []
        self.error: str | None = None
        self.cancelled: bool = False
        self.replaygain_notes: list[str] = []
        self.log_path: Path | None = None

    def configure_from_job(self, job) -> None:
        self.device = job.device
        self.sample_offset = job.sample_offset
        self.encode_format = job.encode_format
        self.flac_compression = job.flac_compression
        self.mp3_bitrate = job.mp3_bitrate
        self.opus_bitrate = job.opus_bitrate
        self.burst_fallback = job.burst_fallback
        self.verify_accuraterip = job.verify_accuraterip
        self.apply_replaygain = job.apply_replaygain
        self.embed_artwork = job.embed_artwork
        self.test_and_copy = getattr(job, 'test_and_copy', True)
        self.write_cue_file = getattr(job, 'write_cue_file', True)
        self.defeat_audio_cache = getattr(job, 'defeat_audio_cache', True)
        self.rip_htoa = getattr(job, 'rip_htoa', True)
        self.drive_caches = getattr(job, 'drive_caches_audio', None)
        self.cache_message = getattr(job, 'drive_cache_message', '') or ''
        self.accurate_stream = getattr(job, 'drive_accurate_stream', None)
        self.accurate_stream_message = (
            getattr(job, 'drive_accurate_stream_message', '') or ''
        )
        self.c2_pointers = getattr(job, 'drive_c2_pointers', None)
        self.c2_message = getattr(job, 'drive_c2_message', '') or ''
        self.album = job.album
        self.disc = job.disc_info

    def begin_track(
        self,
        number: int,
        *,
        title: str = '',
        length_label: str = '',
        start_sector: int = 0,
        length_sectors: int = 0,
    ) -> TrackLogEntry:
        entry = TrackLogEntry(
            number=number,
            title=title,
            length_label=length_label,
            start_sector=start_sector,
            length_sectors=length_sectors,
        )
        self.tracks.append(entry)
        return entry

    def finish(self, *, error: str | None = None, cancelled: bool = False) -> None:
        self.finished_at = datetime.now()
        self.error = error
        self.cancelled = cancelled

    def render(self) -> str:
        lines: list[str] = []
        app = config.APPLICATION_NAME
        ver = config.APPLICATION_VERSION
        lines.append(f'{app} {ver}  —  extraction logfile')
        lines.append('')
        lines.append(
            f'{app} extraction logfile from {self.started_at.strftime("%d. %B %Y, %H:%M")}'
        )
        if self.finished_at is not None:
            lines.append(
                f'Finished: {self.finished_at.strftime("%d. %B %Y, %H:%M")}'
            )
        lines.append('')

        artist = (self.album.artist if self.album else '') or 'Unknown Artist'
        album = (self.album.title if self.album else '') or 'Unknown Album'
        lines.append(f'{artist} / {album}')
        if self.album and self.album.date:
            lines.append(f'Release date: {self.album.date}')
        if self.album and self.album.musicbrainz_release_id:
            lines.append(f'MusicBrainz release: {self.album.musicbrainz_release_id}')
        lines.append('')

        lines.append(f'Used drive  : {self.device or "unknown"}')
        lines.append(f'Host system : {platform.system()} {platform.release()} ({platform.machine()})')
        lines.append('')

        lines.append(
            'Read mode               : Secure (cdparanoia full paranoia, '
            'never-skip + abort-on-skip)'
        )
        lines.append(
            'Read command            : cdparanoia -w --never-skip=200 -X '
            '(-O offset when calibrated)'
        )
        lines.append(
            'Test & copy             : Yes'
            if self.test_and_copy
            else 'Test & copy             : No'
        )
        lines.append(
            'Burst fallback          : Yes' if self.burst_fallback else 'Burst fallback          : No'
        )
        if self.accurate_stream is True:
            astream = 'Yes'
        elif self.accurate_stream is False:
            astream = 'No'
        else:
            astream = 'Unknown (run Drive setup)'
        lines.append(f'Utilize accurate stream : {astream}')
        if self.accurate_stream_message:
            lines.append(f'Accurate Stream test    : {self.accurate_stream_message}')
        if self.drive_caches is True:
            defeat = 'Yes (detected; flush between test/copy)'
        elif self.drive_caches is False:
            defeat = (
                'Yes (not detected; still flush between test/copy)'
                if self.defeat_audio_cache
                else 'No (not detected)'
            )
        else:
            defeat = 'Yes' if self.defeat_audio_cache else 'No'
        lines.append(f'Defeat audio cache      : {defeat}')
        if self.cache_message:
            lines.append(f'Cache analysis          : {self.cache_message}')
        # cdparanoia does not consume C2 data; we still report hardware support.
        if self.c2_pointers is True:
            c2 = 'Yes (drive supports; not used by cdparanoia path)'
        elif self.c2_pointers is False:
            c2 = 'No'
        else:
            c2 = 'Unknown (run Drive setup)'
        lines.append(f'Make use of C2 pointers : {c2}')
        if self.c2_message:
            lines.append(f'C2 pointer test         : {self.c2_message}')
        lines.append(
            'Rip HTOA / pregap       : Yes (EAC-style)'
            if self.rip_htoa
            else 'Rip HTOA / pregap       : No'
        )
        lines.append('')
        lines.append(f'Read offset correction                      : {self.sample_offset}')
        lines.append(
            'Overread into Lead-In and Lead-Out          : No'
        )
        lines.append(
            'Fill up missing offset samples with silence : '
            'Yes (cdparanoia -O / AccurateRip edges)'
        )
        lines.append('Delete leading and trailing silent blocks   : No')
        lines.append('Null samples used in CRC calculations       : Yes')
        lines.append(
            'Used interface                              : Linux /dev (cdparanoia / libcdio-paranoia)'
        )
        lines.append(
            'Gap handling                                : '
            'Track 1 pregap ignored if ≤2s; longer non-silent HTOA → track 00; '
            'track 1 never includes INDEX 00'
        )
        lines.append(
            'Error recovery                              : '
            'Full paranoia re-read + jitter fixup; never-skip=200; abort on skip (-X)'
        )
        lines.append('')

        lines.append(f'Used output format              : {self.encode_format.upper()}')
        if self.encode_format == 'flac':
            lines.append(f'FLAC compression level          : {self.flac_compression}')
            lines.append('Selected bitrate                : lossless')
        elif self.encode_format == 'mp3':
            lines.append(f'Selected bitrate                : {self.mp3_bitrate} kBit/s CBR')
        elif self.encode_format == 'opus':
            lines.append(f'Selected bitrate                : {self.opus_bitrate} kBit/s')
        else:
            lines.append('Selected bitrate                : uncompressed PCM')
        lines.append(f'Add tags                        : Yes')
        lines.append(f'Embed artwork                   : {"Yes" if self.embed_artwork else "No"}')
        lines.append(f'ReplayGain                      : {"Yes" if self.apply_replaygain else "No"}')
        lines.append(f'AccurateRip                     : {"Yes" if self.verify_accuraterip else "No"}')
        if self.ar_status:
            lines.append(f'AccurateRip database            : {self.ar_status}')
        lines.append(
            'Write CUE sheet                 : Yes '
            '(EAC multi-file / left-out gaps, or image CUE)'
            if self.write_cue_file
            else 'Write CUE sheet                 : No'
        )
        lines.append('')

        if self.disc and self.disc.tracks:
            lines.append('TOC of the extracted CD')
            lines.append('')
            lines.append('     Track |   Start  |  Length  | Start sector | End sector')
            lines.append('    ---------------------------------------------------------')
            for t in self.disc.tracks:
                end = t.start_sector + t.length_sectors - 1
                start_lab = _msf_from_sectors(t.start_sector)
                len_lab = t.duration_label if hasattr(t, 'duration_label') else _msf_from_sectors(t.length_sectors)
                # duration_label is M:SS; expand to M:SS.FF when possible
                len_lab = _msf_from_sectors(t.length_sectors)
                lines.append(
                    f'       {t.number:2d}  | {start_lab:>8s} | {len_lab:>8s} | '
                    f'{t.start_sector:9d}    | {end:8d}'
                )
            lines.append('')

        for entry in self.tracks:
            lines.append(f'Track {entry.number:2d}')
            lines.append('')
            if entry.output_path is not None:
                lines.append(f'     Filename {entry.output_path}')
            elif entry.filename:
                lines.append(f'     Filename {entry.filename}')
            if entry.title:
                lines.append(f'     Title    {entry.title}')
            lines.append('')
            if entry.peak_percent is not None:
                lines.append(f'     Peak level {entry.peak_percent:.1f} %')
            if entry.extract_speed_x is not None:
                lines.append(f'     Extraction speed {entry.extract_speed_x:.1f} X')
            if entry.extract_seconds is not None:
                lines.append(f'     Extraction time {entry.extract_seconds:.1f} s')
            if entry.quality_percent is not None:
                lines.append(f'     Track quality {entry.quality_percent:.1f} %')
            elif entry.error_correction_lines:
                # First line from summary is usually Track quality …
                for ec in entry.error_correction_lines:
                    if ec.lower().startswith('track quality'):
                        lines.append(f'     {ec}')
                        break
            lines.append(f'     Extraction mode {entry.extract_mode}')
            if entry.test_crc:
                lines.append(f'     Test CRC {entry.test_crc.upper()}')
            if entry.copy_crc:
                lines.append(f'     Copy CRC {entry.copy_crc.upper()}')
            if entry.test_crc and entry.copy_crc:
                if entry.test_crc == entry.copy_crc:
                    lines.append('     Test and Copy CRCs matched')
                else:
                    lines.append('     WARNING: Test and Copy CRCs differ')
            if entry.accuraterip is not None:
                lines.append(f'     {_format_ar_line(entry.accuraterip)}')
            else:
                lines.append('     AccurateRip not checked')
            # EAC-style error correction block (skip duplicate quality line).
            for ec in entry.error_correction_lines:
                if ec.lower().startswith('track quality'):
                    continue
                lines.append(f'     {ec}')
            for note in entry.notes:
                lines.append(f'     Note: {note}')
            if entry.had_errors and entry.status.upper() in ('OK', 'FINISHED'):
                lines.append(f'     Copy finished with errors')
            else:
                lines.append(f'     Copy {entry.status}')
            lines.append('')

        # Summary
        ar_checked = [t for t in self.tracks if t.accuraterip is not None]
        if ar_checked:
            matches = sum(
                1
                for t in ar_checked
                if t.accuraterip
                and t.accuraterip.confidence == AccurateRipConfidence.MATCH
            )
            if matches == len(ar_checked):
                lines.append('All tracks accurately ripped')
            elif matches == 0:
                lines.append('No tracks could be verified as accurate')
            else:
                lines.append(
                    f'{matches} of {len(ar_checked)} tracks accurately ripped'
                )
            lines.append('')

        burst = [
            t.number
            for t in self.tracks
            if 'burst' in (t.extract_mode or '').lower()
        ]
        if burst:
            lines.append(
                f'Burst mode used for track(s): {", ".join(str(n) for n in burst)}'
            )
            lines.append(
                '(Secure extraction failed or incomplete; re-ripped with paranoia disabled)'
            )
            lines.append('')

        errored = [t.number for t in self.tracks if t.had_errors]
        if errored:
            lines.append(
                f'There were errors on track(s): {", ".join(str(n) for n in errored)}'
            )
            lines.append('')

        if self.replaygain_notes:
            lines.append('ReplayGain:')
            for n in self.replaygain_notes:
                lines.append(f'  {n}')
            lines.append('')

        if self.notes:
            lines.append('Additional notes:')
            for n in self.notes:
                lines.append(f'  {n}')
            lines.append('')

        if self.cancelled:
            lines.append('Extraction cancelled by user')
        elif self.error:
            lines.append(f'Errors occurred: {self.error}')
        elif any(t.had_errors for t in self.tracks):
            lines.append(
                'Some tracks had read errors or required heavy correction '
                '(see per-track details above)'
            )
        else:
            lines.append('No errors occurred')
        lines.append('')
        lines.append('End of status report')
        lines.append('')
        # Simple checksum of log body (EAC has "==== Log checksum ... ====")
        body = '\n'.join(lines)
        checksum = zlib.crc32(body.encode('utf-8')) & 0xFFFFFFFF
        lines.append(f'==== Log checksum {checksum:08X} ====')
        lines.append('')
        return '\n'.join(lines)

    def default_log_filename(self) -> str:
        artist = sanitize_component(
            (self.album.artist if self.album else '') or 'Unknown Artist'
        )
        album = sanitize_component(
            (self.album.title if self.album else '') or 'Unknown Album'
        )
        stamp = self.started_at.strftime('%Y-%m-%d')
        return f'{artist} - {album} ({stamp}).log'

    def write(self, directory: Path, filename: str | None = None) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        name = filename or self.default_log_filename()
        path = directory / name
        path.write_text(self.render(), encoding='utf-8')
        self.log_path = path
        return path


def analyze_wav_for_log(
    wav_path: Path,
    _length_sectors: int = 0,
) -> tuple[str, float | None, float | None]:
    """Return ``(crc32_hex, peak_percent, None)`` for a CDDA WAV file."""
    crc = 0
    peak = 0
    try:
        with wave.open(str(wav_path), 'rb') as wf:
            if wf.getsampwidth() != 2:
                raw = wf.readframes(wf.getnframes())
                crc = zlib.crc32(raw) & 0xFFFFFFFF
                return f'{crc:08x}', None, None
            remaining = wf.getnframes()
            chunk = 65536
            while remaining > 0:
                take = min(chunk, remaining)
                data = wf.readframes(take)
                remaining -= take
                crc = zlib.crc32(data, crc)
                for i in range(0, len(data) - 1, 2):
                    sample = int.from_bytes(data[i : i + 2], 'little', signed=True)
                    a = abs(sample)
                    if a > peak:
                        peak = a
            crc &= 0xFFFFFFFF
    except Exception:  # noqa: BLE001
        try:
            raw = wav_path.read_bytes()
            pcm = raw[44:] if len(raw) > 44 and raw[:4] == b'RIFF' else raw
            crc = zlib.crc32(pcm) & 0xFFFFFFFF
            return f'{crc:08x}', None, None
        except OSError:
            return '', None, None

    peak_pct = (peak / 32767.0) * 100.0 if peak else 0.0
    return f'{crc:08x}', peak_pct, None


def extraction_speed_x(length_sectors: int, elapsed_seconds: float) -> float | None:
    if elapsed_seconds <= 0 or length_sectors <= 0:
        return None
    # Realtime audio duration at 75 sectors/sec
    audio_seconds = length_sectors / 75.0
    return audio_seconds / elapsed_seconds


def title_for_track(album: AlbumMetadata | None, number: int) -> str:
    meta = track_meta_for(album, number)
    if meta and meta.title:
        return meta.title
    return f'Track {number:02d}'


def _msf_from_sectors(sectors: int) -> str:
    if sectors < 0:
        sectors = 0
    frames = sectors % 75
    total_sec = sectors // 75
    minutes, seconds = divmod(total_sec, 60)
    return f'{minutes:2d}:{seconds:02d}.{frames:02d}'


def _format_ar_line(ar: AccurateRipResult) -> str:
    if ar.confidence == AccurateRipConfidence.MATCH:
        return (
            f'Accurately ripped (confidence {ar.confidence_count})  '
            f'[{ar.matched_version} CRC {ar.crc_v1 if ar.matched_version == "v1" else ar.crc_v2}]'
        )
    if ar.confidence == AccurateRipConfidence.MISMATCH:
        return (
            f'Cannot be verified as accurate (confidence {ar.confidence_count})  '
            f'[v1 {ar.crc_v1}  v2 {ar.crc_v2}]'
        )
    if ar.confidence == AccurateRipConfidence.NOT_IN_DB:
        return 'Track not present in AccurateRip database'
    if ar.confidence == AccurateRipConfidence.ERROR:
        return f'AccurateRip error: {ar.message}'
    return f'AccurateRip: {ar.message or "unknown"}'
