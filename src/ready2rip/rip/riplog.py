# SPDX-License-Identifier: GPL-3.0-or-later
"""Detailed rip status log."""

from __future__ import annotations

import array
import platform
import sys
import wave
import zlib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ready2rip import config
from ready2rip.accuraterip import AccurateRipConfidence, AccurateRipResult
from ready2rip.ctdb import CtdbConfidence, CtdbResult
from ready2rip.disc.probe import DiscInfo
from ready2rip.metadata.providers import AlbumMetadata
from ready2rip.paths import sanitize_component, track_meta_for
from ready2rip.rip.eac_logcrc import append_eac_checksum
from ready2rip.rip.encode import encode_command_summary
from ready2rip.settings_defs import (
    rip_log_aac_quality,
    rip_log_mp3_quality,
    rip_log_qaac_quality,
)


@dataclass
class TrackLogEntry:
    number: int
    title: str = ''
    filename: str = ''
    output_path: Path | None = None
    length_label: str = ''
    start_sector: int = 0
    length_sectors: int = 0
    # Human mode: e.g. "secure (test & copy)", "burst (full-disc after CRC mismatch)"
    extract_mode: str = 'secure'
    extract_seconds: float | None = None
    extract_speed_x: float | None = None
    peak_percent: float | None = None
    # Quality 0–100 from paranoia fixups / skips; None when not measured (burst).
    quality_percent: float | None = None
    # PCM CRC32 of the test pass (empty when no dual-pass test).
    test_crc: str = ''
    # PCM CRC32 of the audio that was encoded (copy or single-pass).
    copy_crc: str = ''
    # True/False only when a real dual-pass test&copy was performed.
    crc_verified: bool | None = None
    # Expected CDDA size (WAV header + PCM) vs actual file size.
    expected_wav_bytes: int = 0
    wav_bytes: int = 0
    accuraterip: AccurateRipResult | None = None
    ctdb: CtdbResult | None = None
    status: str = 'OK'
    notes: list[str] = field(default_factory=list)
    # Chronological attempt history (retries, CRC mismatches, mode switches).
    attempt_log: list[str] = field(default_factory=list)
    # Pre-rendered error-correction lines (quality, counters, suspicious MSF).
    error_correction_lines: list[str] = field(default_factory=list)
    had_errors: bool = False


class RipLog:
    """Builds a human-readable extraction log for secure rips."""

    def __init__(self) -> None:
        self.started_at = datetime.now()
        self.finished_at: datetime | None = None
        self.device: str = ''
        self.sample_offset: int = 0
        self.offset_configured: bool = False
        self.overread_label: str = 'No'
        self.encode_format: str = 'flac'
        self.flac_compression: int = 5
        self.wavpack_compression: str = 'high'
        self.mp3_quality: str = 'v0'
        self.aac_quality: str = 'cbr256'
        self.aac_bitrate: int = 256
        self.qaac_quality: str = 'cvbr256'
        self.opus_bitrate: int = 160
        self.also_encode: bool = False
        self.also_encode_format: str = 'mp3'
        self.also_encode_folder: str = 'format_subdir'
        self.also_encode_directory: str = ''
        self.burst_mode: bool = False
        self.burst_fallback: bool = False
        self.verify_accuraterip: bool = True
        self.verify_ctdb: bool = True
        self.submit_ctdb: bool = False
        self.apply_replaygain: bool = True
        self.write_sound_check: bool = True
        self.add_ripper_tag: bool = True
        self.embed_lyrics: bool = True
        self.lyrics_save_mode: str = 'both'
        self.embed_artwork: bool = True
        self.embed_artwork_lossless: bool = False
        self.test_and_copy: bool = True
        self.write_cue_file: bool = True
        self.defeat_audio_cache: bool = True
        self.rip_htoa: bool = True
        self.rip_data_tracks: bool = True
        self.album: AlbumMetadata | None = None
        self.disc: DiscInfo | None = None
        self.ar_status: str = ''
        self.ctdb_status: str = ''
        self.tracks: list[TrackLogEntry] = []
        self.notes: list[str] = []
        # Session-level archival history (e.g. superseded secure rips before
        # a full-disc burst re-rip). Always printed in the log.
        self.history: list[str] = []
        self.error: str | None = None
        self.cancelled: bool = False
        self.replaygain_notes: list[str] = []
        self.log_path: Path | None = None
        # Max secure test&copy retries configured for this job.
        self.test_copy_max_retries: int = 3

    def configure_from_job(self, job) -> None:
        self.device = job.device
        self.sample_offset = job.sample_offset
        self.offset_configured = bool(getattr(job, 'offset_configured', False))
        overread = getattr(job, 'overread', None)
        self.overread_label = overread.log_label if overread is not None else 'No'
        self.encode_format = job.encode_format
        self.flac_compression = job.flac_compression
        self.wavpack_compression = getattr(job, 'wavpack_compression', None) or 'high'
        self.mp3_quality = job.mp3_quality or 'v0'
        self.aac_quality = getattr(job, 'aac_quality', '') or (
            f'cbr{int(job.aac_bitrate)}' if getattr(job, 'aac_bitrate', 0) else 'cbr256'
        )
        self.aac_bitrate = job.aac_bitrate
        self.qaac_quality = getattr(job, 'qaac_quality', None) or 'cvbr256'
        self.opus_bitrate = job.opus_bitrate
        self.also_encode = bool(getattr(job, 'also_encode', False))
        self.also_encode_format = getattr(job, 'also_encode_format', None) or 'mp3'
        self.also_encode_folder = getattr(job, 'also_encode_folder', None) or 'format_subdir'
        self.also_encode_directory = getattr(job, 'also_encode_directory', None) or ''
        self.burst_mode = bool(getattr(job, 'burst_mode', False))
        self.burst_fallback = job.burst_fallback
        self.verify_accuraterip = job.verify_accuraterip
        self.verify_ctdb = getattr(job, 'verify_ctdb', True)
        self.submit_ctdb = getattr(job, 'submit_ctdb', False)
        self.apply_replaygain = job.apply_replaygain
        self.write_sound_check = bool(getattr(job, 'write_sound_check', True))
        self.add_ripper_tag = bool(getattr(job, 'add_ripper_tag', True))
        self.embed_lyrics = bool(getattr(job, 'embed_lyrics', True))
        self.lyrics_save_mode = getattr(job, 'lyrics_save_mode', None) or 'both'
        self.embed_artwork = job.embed_artwork
        self.embed_artwork_lossless = bool(getattr(job, 'embed_artwork_lossless', False))
        self.test_and_copy = getattr(job, 'test_and_copy', True)
        self.write_cue_file = getattr(job, 'write_cue_file', True)
        self.defeat_audio_cache = getattr(job, 'defeat_audio_cache', True)
        self.rip_htoa = getattr(job, 'rip_htoa', True)
        self.rip_data_tracks = getattr(job, 'rip_data_tracks', True)
        self.album = job.album
        self.disc = job.disc_info
        self.test_copy_max_retries = int(getattr(job, 'test_copy_max_retries', 3) or 0)

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
        """Build a compact but complete rip log (EAC-style checksum appended)."""
        lines: list[str] = []
        app = config.APPLICATION_NAME
        ver = config.APPLICATION_VERSION
        stamp = self.started_at.strftime('%d %B %Y, %H:%M')
        lines.append(f'{app} {ver} — rip log')
        lines.append(f'Started:  {stamp}')
        if self.finished_at is not None:
            lines.append(f'Finished: {self.finished_at.strftime("%d %B %Y, %H:%M")}')
        lines.append('')

        artist = (self.album.artist if self.album else '') or 'Unknown Artist'
        album = (self.album.title if self.album else '') or 'Unknown Album'
        lines.append(f'{artist} / {album}')
        if self.album and self.album.date:
            lines.append(_kv('Release date', self.album.date))
        if self.album and self.album.musicbrainz_release_id:
            lines.append(_kv('MusicBrainz', self.album.musicbrainz_release_id))
        lines.append('')

        self._render_drive_section(lines)
        self._render_extraction_section(lines)
        self._render_encode_section(lines)
        self._render_verify_section(lines)
        self._render_toc_section(lines)
        self._render_tracks_section(lines)
        self._render_summary_section(lines)

        lines.append('End of status report')
        lines.append('')
        return append_eac_checksum('\n'.join(lines))

    def _render_drive_section(self, lines: list[str]) -> None:
        lines.append('== Drive ==')
        lines.append(_kv('Device', self.device or 'unknown'))
        lines.append(
            _kv(
                'Host',
                f'{platform.system()} {platform.release()} ({platform.machine()})',
            )
        )
        offset_note = (
            'calibrated' if self.offset_configured else 'NOT CALIBRATED — AccurateRip may mismatch'
        )
        lines.append(_kv('Read offset', f'{self.sample_offset} ({offset_note})'))
        lines.append(_kv('Overread', self.overread_label))
        if self.overread_label == 'Yes':
            fill = 'No — drive overreads; real samples at edges'
        elif self.overread_label == 'No':
            fill = 'Yes — silence at AccurateRip edges'
        else:
            fill = 'Partial — silence where overread unavailable'
        lines.append(_kv('Missing samples', fill))
        lines.append(_kv('Interface', 'Linux /dev (cdparanoia)'))
        lines.append(_kv('CRC', 'CRC-32 of PCM (nulls in; WAV header out)'))
        lines.append('')

    def _render_extraction_section(self, lines: list[str]) -> None:
        final_modes = [(t.extract_mode or '').lower() for t in self.tracks if t.number > 0]
        any_burst = any('burst' in m for m in final_modes)
        all_burst = bool(final_modes) and all('burst' in m for m in final_modes)
        if self.burst_mode or all_burst:
            read_mode = 'Burst (-Z, paranoia off)'
            read_cmd = 'cdparanoia -w -Z -e (-O when calibrated)'
        elif any_burst:
            read_mode = 'Secure + burst fallback (some tracks -Z)'
            read_cmd = 'cdparanoia -w --never-skip=500 -X -o 8 (burst: -Z)'
        else:
            read_mode = 'Secure (full paranoia)'
            read_cmd = 'cdparanoia -w --never-skip=500 -X -o 8 (-O when calibrated)'

        lines.append('== Extraction ==')
        lines.append(_kv('Mode', read_mode))
        lines.append(_kv('Command', read_cmd))
        if self.burst_mode:
            lines.append(_kv('Test & copy', 'No (burst mode)'))
        elif self.test_and_copy:
            lines.append(
                _kv('Test & copy', f'Yes (max {max(0, self.test_copy_max_retries)} CRC retries)')
            )
        else:
            lines.append(_kv('Test & copy', 'No'))
        lines.append(_kv('Burst mode', _yes_no(self.burst_mode)))
        lines.append(
            _kv('Burst fallback', _yes_no(self.burst_fallback and not self.burst_mode))
        )
        lines.append(
            _kv(
                'Cache flush',
                'Yes (between test/copy)' if self.defeat_audio_cache else 'No',
            )
        )
        lines.append(_kv('HTOA / pregap', _yes_no(self.rip_htoa)))
        lines.append(_kv('Data tracks', _yes_no(self.rip_data_tracks)))
        if self.disc is not None and getattr(self.disc, 'pregap_source', ''):
            lines.append(_kv('Pregap source', self.disc.pregap_source))
        lines.append(
            _kv(
                'Gaps',
                'INDEX 00 → previous; 2s pause → PREGAP; non-silent HTOA → track 00',
            )
        )
        lines.append(
            _kv(
                'Recovery',
                'paranoia re-read; CRC mismatch → retries; else fail '
                '(or -Z if burst fallback)',
            )
        )
        lines.append('')

    def _render_encode_section(self, lines: list[str]) -> None:
        lines.append('== Encode ==')
        fmt = (self.encode_format or 'flac').strip().lower()
        if fmt == 'flac':
            bitrate = f'lossless (FLAC level {self.flac_compression})'
        elif fmt == 'wavpack':
            from ready2rip.settings_defs import rip_log_wavpack_level

            bitrate = f'lossless ({rip_log_wavpack_level(self.wavpack_compression)})'
        elif fmt == 'alac':
            bitrate = 'lossless (ALAC)'
        elif fmt == 'mp3':
            bitrate = rip_log_mp3_quality(self.mp3_quality)
        elif fmt == 'aac':
            bitrate = rip_log_aac_quality(self.aac_quality)
        elif fmt == 'qaac':
            bitrate = rip_log_qaac_quality(self.qaac_quality)
        elif fmt == 'opus':
            bitrate = f'{self.opus_bitrate} kBit/s VBR'
        else:
            bitrate = 'uncompressed PCM'
        lines.append(_kv('Format', f'{fmt.upper()} — {bitrate}'))
        lines.append(
            _kv(
                'Encoder',
                encode_command_summary(
                    self.encode_format,
                    flac_compression=self.flac_compression,
                    wavpack_compression=self.wavpack_compression,
                    mp3_quality=self.mp3_quality,
                    aac_quality=self.aac_quality,
                    qaac_quality=self.qaac_quality,
                    opus_bitrate=self.opus_bitrate,
                ),
            )
        )
        if self.also_encode:
            also_fmt = (self.also_encode_format or 'mp3').strip().lower()
            also_label = {
                'mp3': 'LAME MP3',
                'aac': 'FDK AAC',
                'qaac': 'QAAC',
                'opus': 'Opus',
            }.get(also_fmt, also_fmt.upper())
            layout = self.also_encode_folder or 'format_subdir'
            if layout == 'custom_folder' and self.also_encode_directory:
                layout_note = f'custom → {self.also_encode_directory}'
            else:
                layout_note = layout
            lines.append(_kv('Also encode', f'{also_label} ({layout_note})'))
            lines.append(
                _kv(
                    'Lossy encoder',
                    encode_command_summary(
                        self.also_encode_format,
                        flac_compression=self.flac_compression,
                        mp3_quality=self.mp3_quality,
                        aac_quality=self.aac_quality,
                        qaac_quality=self.qaac_quality,
                        opus_bitrate=self.opus_bitrate,
                    ),
                )
            )
        lines.append(_kv('Pre-process', 'None'))
        lines.append(_kv('Tags', 'Yes (Encoded by = tool + version)'))
        lines.append(_kv('Ripper tag', _yes_no(self.add_ripper_tag)))
        if not self.embed_lyrics:
            lyrics_label = 'No'
        else:
            mode = (self.lyrics_save_mode or 'both').strip().lower()
            lyrics_label = {
                'embed': 'Yes (embed)',
                'sidecar': 'Yes (sidecar)',
                'both': 'Yes (embed + sidecar)',
            }.get(mode, f'Yes ({mode})')
        lines.append(_kv('Lyrics', lyrics_label))
        if self.embed_artwork and self.embed_artwork_lossless:
            embed_label = 'Yes (lossy + lossless)'
        elif self.embed_artwork:
            embed_label = 'Yes (lossy)'
        elif self.embed_artwork_lossless:
            embed_label = 'Yes (lossless)'
        else:
            embed_label = 'No'
        lines.append(_kv('Artwork', embed_label))
        lines.append(_kv('ReplayGain', _yes_no(self.apply_replaygain)))
        lines.append(_kv('Sound Check', _yes_no(self.write_sound_check)))
        lines.append('')

    def _render_verify_section(self, lines: list[str]) -> None:
        lines.append('== Verify ==')
        lines.append(_kv('AccurateRip', _yes_no(self.verify_accuraterip)))
        if self.ar_status:
            lines.append(_kv('AR database', self.ar_status))
        lines.append(_kv('CTDB', _yes_no(self.verify_ctdb)))
        if self.ctdb_status:
            lines.append(_kv('CTDB database', self.ctdb_status))
        lines.append(_kv('CTDB submit', _yes_no(self.submit_ctdb)))
        lines.append(
            _kv(
                'CUE sheet',
                'Yes (multi-file / image)' if self.write_cue_file else 'No',
            )
        )
        lines.append('')

    def _render_toc_section(self, lines: list[str]) -> None:
        if not (self.disc and self.disc.tracks):
            return
        lines.append('== TOC ==')
        lines.append('Track |   Start  |  Length  |   Pregap | Start sec | End sec')
        lines.append('------+----------+----------+----------+-----------+--------')
        for t in self.disc.tracks:
            end = t.start_sector + t.length_sectors - 1
            lines.append(
                f'{t.number:5d} | {_msf_from_sectors(t.start_sector):>8s} | '
                f'{_msf_from_sectors(t.length_sectors):>8s} | '
                f'{_msf_from_sectors(t.pregap_sectors):>8s} | '
                f'{t.start_sector:9d} | {end:7d}'
            )
        for d in getattr(self.disc, 'data_tracks', None) or []:
            end = d.start_sector + d.length_sectors - 1
            lines.append(
                f'{d.number:5d} | {_msf_from_sectors(d.start_sector):>8s} | '
                f'{_msf_from_sectors(d.length_sectors):>8s} | '
                f'{_msf_from_sectors(d.pregap_sectors):>8s} | '
                f'{d.start_sector:9d} | {end:7d}  '
                f'{d.cue_mode} session {d.session}'
            )
        lines.append('')

    def _render_tracks_section(self, lines: list[str]) -> None:
        burst_tracks = [
            t.number for t in self.tracks if 'burst' in (t.extract_mode or '').lower()
        ]
        if burst_tracks:
            lines.append(
                f'WARNING: burst (-Z) on track(s) '
                f'{", ".join(str(n) for n in burst_tracks)} — not archival-grade'
            )
            lines.append('')

        for entry in self.tracks:
            title = entry.title.strip() if entry.title else ''
            heading = f'Track {entry.number:02d}'
            if title:
                heading = f'{heading} — {title}'
            if entry.length_label:
                heading = f'{heading} ({entry.length_label})'
            lines.append(heading)
            if entry.output_path is not None:
                lines.append(f'  File: {entry.output_path}')
            elif entry.filename:
                lines.append(f'  File: {entry.filename}')

            stats: list[str] = []
            if entry.peak_percent is not None:
                stats.append(f'peak {entry.peak_percent:.1f}%')
            if entry.extract_speed_x is not None:
                stats.append(f'{entry.extract_speed_x:.1f}×')
            if entry.extract_seconds is not None:
                stats.append(f'{entry.extract_seconds:.1f}s')
            quality = entry.quality_percent
            if quality is None:
                for ec in entry.error_correction_lines:
                    if ec.lower().startswith('track quality'):
                        # Keep "Track quality 100.0 %" style from the source line.
                        stats.append(ec)
                        break
            else:
                stats.append(f'quality {quality:.1f}%')
            if stats:
                lines.append(f'  {" · ".join(stats)}')

            lines.append(f'  Mode: {entry.extract_mode}')
            if entry.expected_wav_bytes > 0 and entry.wav_bytes > 0:
                delta = entry.wav_bytes - entry.expected_wav_bytes
                lines.append(
                    f'  WAV: {entry.wav_bytes} bytes '
                    f'(expected ~{entry.expected_wav_bytes}, Δ {delta:+d})'
                )
            elif entry.wav_bytes > 0:
                lines.append(f'  WAV: {entry.wav_bytes} bytes')

            if entry.crc_verified is not None:
                if entry.test_crc:
                    lines.append(f'  Test CRC: {entry.test_crc.upper()}')
                if entry.copy_crc:
                    lines.append(f'  Copy CRC: {entry.copy_crc.upper()}')
                lines.append(
                    '  Test & copy: matched'
                    if entry.crc_verified
                    else '  Test & copy: WARNING — CRCs differ'
                )
            elif entry.copy_crc:
                lines.append(f'  Audio CRC: {entry.copy_crc.upper()} (single-pass)')

            raw_mode = (entry.extract_mode or '').lower().startswith('raw')
            if entry.accuraterip is not None:
                lines.append(f'  {_format_ar_line(entry.accuraterip)}')
            elif self.verify_accuraterip and not raw_mode:
                lines.append('  AccurateRip: not checked')
            if entry.ctdb is not None:
                lines.append(f'  {_format_ctdb_line(entry.ctdb)}')
            elif self.verify_ctdb and not raw_mode:
                lines.append('  CTDB: not checked')

            for ec in entry.error_correction_lines:
                if ec.lower().startswith('track quality'):
                    continue
                lines.append(f'  {ec}')
            for line in entry.attempt_log:
                lines.append(f'  Attempt: {line}')
            for note in entry.notes:
                lines.append(f'  Note: {note}')

            if entry.had_errors and entry.status.upper() in ('OK', 'FINISHED'):
                lines.append('  Result: finished with errors')
            else:
                lines.append(f'  Result: {entry.status}')
            lines.append('')

    def _render_summary_section(self, lines: list[str]) -> None:
        lines.append('== Summary ==')
        ar_checked = [t for t in self.tracks if t.accuraterip is not None]
        if ar_checked:
            matches = sum(
                1
                for t in ar_checked
                if t.accuraterip and t.accuraterip.confidence == AccurateRipConfidence.MATCH
            )
            if matches == len(ar_checked):
                lines.append('AccurateRip: all tracks matched')
            elif matches == 0:
                lines.append('AccurateRip: no tracks verified')
            else:
                lines.append(f'AccurateRip: {matches}/{len(ar_checked)} tracks matched')

        ctdb_checked = [t for t in self.tracks if t.ctdb is not None]
        if ctdb_checked:
            matches = sum(
                1 for t in ctdb_checked if t.ctdb and t.ctdb.confidence == CtdbConfidence.MATCH
            )
            if matches == len(ctdb_checked):
                lines.append('CTDB: all tracks matched')
            elif matches == 0:
                lines.append('CTDB: no tracks verified')
            else:
                lines.append(f'CTDB: {matches}/{len(ctdb_checked)} tracks matched')

        burst = [t.number for t in self.tracks if 'burst' in (t.extract_mode or '').lower()]
        if burst:
            lines.append(
                f'WARNING: burst (-Z) on track(s) {", ".join(str(n) for n in burst)}'
            )

        crc_ok = [t.number for t in self.tracks if t.crc_verified is True]
        crc_bad = [t.number for t in self.tracks if t.crc_verified is False]
        if crc_ok and not crc_bad:
            lines.append(f'Test & copy CRC: matched on all checked tracks ({len(crc_ok)})')
        else:
            if crc_ok:
                lines.append(
                    f'Test & copy CRC matched: {", ".join(str(n) for n in crc_ok)}'
                )
            if crc_bad:
                lines.append(
                    f'Test & copy CRC MISMATCH: {", ".join(str(n) for n in crc_bad)}'
                )

        errored = [t.number for t in self.tracks if t.had_errors]
        if errored:
            lines.append(f'Errors on track(s): {", ".join(str(n) for n in errored)}')

        if self.history:
            lines.append('Session history:')
            for n in self.history:
                lines.append(f'  {n}')
        if self.replaygain_notes:
            lines.append('ReplayGain:')
            for n in self.replaygain_notes:
                lines.append(f'  {n}')
        if self.notes:
            lines.append('Notes:')
            for n in self.notes:
                lines.append(f'  {n}')

        lines.append('')
        if self.cancelled:
            lines.append('Outcome: cancelled by user')
        elif self.error:
            lines.append(f'Outcome: errors — {self.error}')
        elif any(t.had_errors for t in self.tracks) or crc_bad or burst:
            lines.append(
                'Outcome: completed with corrections, burst fallback, and/or CRC issues'
            )
        else:
            lines.append('Outcome: no errors')
        lines.append('')

    def default_log_filename(self) -> str:
        artist = sanitize_component((self.album.artist if self.album else '') or 'Unknown Artist')
        album = sanitize_component((self.album.title if self.album else '') or 'Unknown Album')
        stamp = self.started_at.strftime('%Y-%m-%d')
        return f'{artist} - {album} ({stamp}).log'

    def write(self, directory: Path, filename: str | None = None) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        name = filename or self.default_log_filename()
        path = directory / name
        path.write_text(self.render(), encoding='utf-8')
        self.log_path = path
        return path


_PCM_CHUNK_FRAMES = 131072  # 256 KiB of 16-bit stereo


def _pcm_crc_from_raw(path: Path) -> str:
    raw = path.read_bytes()
    pcm = raw[44:] if len(raw) > 44 and raw[:4] == b'RIFF' else raw
    return f'{zlib.crc32(pcm) & 0xFFFFFFFF:08x}'


def wav_pcm_crc(wav_path: Path) -> str:
    """PCM CRC32 of a CDDA WAV (no peak scan). Fast path for the test pass."""
    crc = 0
    try:
        with wave.open(str(wav_path), 'rb') as wf:
            remaining = wf.getnframes()
            while remaining > 0:
                take = min(_PCM_CHUNK_FRAMES, remaining)
                data = wf.readframes(take)
                if not data:
                    break
                remaining -= take
                crc = zlib.crc32(data, crc)
        return f'{crc & 0xFFFFFFFF:08x}'
    except Exception:
        try:
            return _pcm_crc_from_raw(wav_path)
        except OSError:
            return ''


def analyze_pcm_for_log(pcm: bytes) -> tuple[str, float | None]:
    """Return ``(crc32_hex, peak_percent)`` from interleaved LE 16-bit stereo PCM."""
    if not pcm:
        return '', None
    crc = zlib.crc32(pcm) & 0xFFFFFFFF
    if len(pcm) % 2 != 0:
        return f'{crc:08x}', None
    samples = array.array('h')
    samples.frombytes(pcm if len(pcm) % 2 == 0 else pcm[:-1])
    if sys.byteorder != 'little':
        samples.byteswap()
    if not samples:
        return f'{crc:08x}', 0.0
    peak = max(abs(min(samples)), abs(max(samples)))
    peak_pct = (peak / 32767.0) * 100.0 if peak else 0.0
    return f'{crc:08x}', peak_pct


def analyze_wav_for_log(
    wav_path: Path,
    _length_sectors: int = 0,
) -> tuple[str, float | None, float | None]:
    """Return ``(crc32_hex, peak_percent, None)`` for a CDDA WAV file."""
    try:
        with wave.open(str(wav_path), 'rb') as wf:
            if wf.getsampwidth() != 2:
                raw = wf.readframes(wf.getnframes())
                crc = zlib.crc32(raw) & 0xFFFFFFFF
                return f'{crc:08x}', None, None
            raw = wf.readframes(wf.getnframes())
        crc, peak = analyze_pcm_for_log(raw)
        return crc, peak, None
    except Exception:
        try:
            return _pcm_crc_from_raw(wav_path), None, None
        except OSError:
            return '', None, None


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


def _kv(label: str, value: str, *, width: int = 18) -> str:
    """Compact ``Label: value`` line for the rip log."""
    return f'{label:<{width}} {value}'


def _yes_no(flag: bool) -> str:
    return 'Yes' if flag else 'No'


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


def _format_ctdb_line(ctdb: CtdbResult) -> str:
    if ctdb.confidence == CtdbConfidence.MATCH:
        return f'CTDB accurately ripped (confidence {ctdb.confidence_count})  [CRC {ctdb.crc}]'
    if ctdb.confidence == CtdbConfidence.MISMATCH:
        return (
            f'CTDB cannot be verified as accurate '
            f'(confidence {ctdb.confidence_count})  [CRC {ctdb.crc}]'
        )
    if ctdb.confidence == CtdbConfidence.NOT_IN_DB:
        return 'Track not present in CUETools Database'
    if ctdb.confidence == CtdbConfidence.ERROR:
        return f'CTDB error: {ctdb.message}'
    return f'CTDB: {ctdb.message or "unknown"}'
