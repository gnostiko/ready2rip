# SPDX-License-Identifier: GPL-3.0-or-later
"""Detailed rip status log."""

from __future__ import annotations

import array
import sys
import wave
import zlib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ready2rip import config
from ready2rip.accuraterip import AccurateRipConfidence, AccurateRipResult
from ready2rip.disc.probe import DiscInfo
from ready2rip.metadata.models import AlbumMetadata
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
        self.rip_mode: str = 'archival'
        self.burst_mode: bool = False
        self.burst_fallback: bool = False
        self.verify_accuraterip: bool = True
        self.require_db_match: bool = False
        self.archival_strict: bool = False
        self.apply_replaygain: bool = True
        # Filled by finish_result before write.
        self.outcome_secure_ok: bool | None = None
        self.outcome_db_verified: bool | None = None
        self.outcome_archival_ok: bool | None = None
        self.outcome_summary: str = ''
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
        self.rip_mode = (getattr(job, 'rip_mode', None) or 'archival').strip().lower()
        self.burst_mode = bool(getattr(job, 'burst_mode', False))
        self.burst_fallback = job.burst_fallback
        self.verify_accuraterip = job.verify_accuraterip
        self.require_db_match = bool(getattr(job, 'require_db_match', False))
        self.archival_strict = bool(getattr(job, 'archival_strict', False))
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
    ) -> TrackLogEntry:
        entry = TrackLogEntry(number=number, title=title)
        self.tracks.append(entry)
        return entry

    def set_outcome(
        self,
        *,
        secure_ok: bool,
        db_verified: bool,
        archival_ok: bool,
        summary: str = '',
    ) -> None:
        """Record archival tiers for the session footer."""
        self.outcome_secure_ok = bool(secure_ok)
        self.outcome_db_verified = bool(db_verified)
        self.outcome_archival_ok = bool(archival_ok)
        self.outcome_summary = (summary or '').strip()

    def finish(self, *, error: str | None = None, cancelled: bool = False) -> None:
        self.finished_at = datetime.now()
        self.error = error
        self.cancelled = cancelled

    def render(self) -> str:
        lines: list[str] = []
        app = config.APPLICATION_NAME
        ver = config.APPLICATION_VERSION
        started = self.started_at.strftime('%d. %B %Y, %H:%M')
        if self.finished_at is not None:
            finished = self.finished_at.strftime('%d. %B %Y, %H:%M')
            lines.append(f'{app} {ver} — {started} → {finished}')
        else:
            lines.append(f'{app} {ver} — {started}')
        lines.append('')

        artist = (self.album.artist if self.album else '') or 'Unknown Artist'
        album = (self.album.title if self.album else '') or 'Unknown Album'
        lines.append(f'{artist} / {album}')
        meta_bits: list[str] = []
        if self.album and self.album.date:
            meta_bits.append(self.album.date)
        if self.album and self.album.musicbrainz_release_id:
            meta_bits.append(f'MB {self.album.musicbrainz_release_id}')
        if meta_bits:
            lines.append(' · '.join(meta_bits))
        lines.append('')

        lines.append(f'Drive: {self.device or "unknown"}')
        offset_note = (
            'calibrated' if self.offset_configured else 'NOT CALIBRATED — AccurateRip may mismatch'
        )
        if self.overread_label == 'Yes':
            fill = 'no silence fill'
        elif self.overread_label == 'No':
            fill = 'silence fill (no overread)'
        else:
            fill = 'partial silence fill'
        lines.append(
            f'Offset: {self.sample_offset} ({offset_note}) · '
            f'Overread: {self.overread_label} · {fill}'
        )
        lines.append('')

        # Reflect what was actually used for the final audio, not only intent.
        final_modes = [(t.extract_mode or '').lower() for t in self.tracks if t.number > 0]
        any_burst = any('burst' in m for m in final_modes)
        all_burst = bool(final_modes) and all('burst' in m for m in final_modes)
        rip_mode = (getattr(self, 'rip_mode', '') or '').strip().lower()
        if self.burst_mode or all_burst or rip_mode == 'burst':
            read_mode = 'Burst (-Z)'
            read_cmd = 'cdparanoia -w -Z -e (-O when calibrated)'
        elif any_burst:
            read_mode = 'Archival + burst fallback'
            read_cmd = 'cdparanoia -w --never-skip=500 -X -o 8 (burst: -Z)'
        elif rip_mode == 'normal':
            read_mode = 'Normal'
            read_cmd = 'cdparanoia -w --never-skip=500 -o 8 (-O when calibrated; repair OK)'
        else:
            read_mode = 'Archival'
            read_cmd = 'cdparanoia -w --never-skip=500 -X -o 8 (-O when calibrated)'
        read_bits = [read_mode]
        if self.burst_mode or rip_mode == 'burst':
            read_bits.append('no test&copy')
        elif self.test_and_copy:
            read_bits.append(f'test&copy (max {max(0, self.test_copy_max_retries)} CRC retry)')
        else:
            read_bits.append('no test&copy')
        if self.burst_fallback and not self.burst_mode and rip_mode != 'burst':
            read_bits.append('burst fallback on')
        if self.defeat_audio_cache:
            read_bits.append('defeat cache')
        else:
            read_bits.append('no defeat cache')
        if not self.rip_htoa:
            read_bits.append('no HTOA')
        if not self.rip_data_tracks:
            read_bits.append('no data tracks')
        lines.append(' · '.join(read_bits))
        lines.append(f'Read: {read_cmd}')
        if self.disc is not None and getattr(self.disc, 'pregap_source', ''):
            lines.append(f'Pregap: {self.disc.pregap_source}')
        lines.append('')

        fmt = (self.encode_format or '').strip().lower()
        encode_bits = [fmt.upper() or 'UNKNOWN']
        if fmt == 'flac':
            encode_bits.append(f'level {self.flac_compression}')
            encode_bits.append('lossless')
        elif fmt == 'wavpack':
            from ready2rip.settings_defs import rip_log_wavpack_level

            encode_bits.append(rip_log_wavpack_level(self.wavpack_compression))
            encode_bits.append('lossless')
        elif fmt == 'alac':
            encode_bits.append('lossless')
        elif fmt == 'mp3':
            encode_bits.append(rip_log_mp3_quality(self.mp3_quality))
        elif fmt == 'aac':
            encode_bits.append(rip_log_aac_quality(self.aac_quality))
        elif fmt == 'opus':
            encode_bits.append(f'{self.opus_bitrate} kBit/s VBR')
        else:
            encode_bits.append('PCM')
        lines.append(' · '.join(encode_bits))
        lines.append(
            'Encoder: '
            + encode_command_summary(
                self.encode_format,
                flac_compression=self.flac_compression,
                wavpack_compression=self.wavpack_compression,
                mp3_quality=self.mp3_quality,
                aac_quality=self.aac_quality,
                qaac_quality=self.qaac_quality,
                opus_bitrate=self.opus_bitrate,
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
            also_bits = [also_label, layout_note]
            if also_fmt == 'qaac':
                also_bits.append(rip_log_qaac_quality(self.qaac_quality))
            lines.append(f'Also encode: {" · ".join(also_bits)}')
            lines.append(
                'Lossy encoder: '
                + encode_command_summary(
                    self.also_encode_format,
                    flac_compression=self.flac_compression,
                    mp3_quality=self.mp3_quality,
                    aac_quality=self.aac_quality,
                    qaac_quality=self.qaac_quality,
                    opus_bitrate=self.opus_bitrate,
                )
            )

        tag_bits = [
            f'Encoded by {"yes" if self.add_ripper_tag else "no"}',
            'Encoder yes',
        ]
        if not self.embed_lyrics:
            tag_bits.append('lyrics no')
        else:
            mode = (self.lyrics_save_mode or 'both').strip().lower()
            lyrics_label = {
                'embed': 'lyrics embed',
                'sidecar': 'lyrics sidecar',
                'both': 'lyrics embed+sidecar',
            }.get(mode, f'lyrics {mode}')
            tag_bits.append(lyrics_label)
        if self.embed_artwork and self.embed_artwork_lossless:
            tag_bits.append('art lossy+lossless')
        elif self.embed_artwork:
            tag_bits.append('art lossy')
        elif self.embed_artwork_lossless:
            tag_bits.append('art lossless')
        else:
            tag_bits.append('art no')
        tag_bits.append(f'RG {"yes" if self.apply_replaygain else "no"}')
        tag_bits.append(f'Sound Check {"yes" if self.write_sound_check else "no"}')
        lines.append('Tags: ' + ' · '.join(tag_bits))

        db_bits = [
            f'AccurateRip {"yes" if self.verify_accuraterip else "no"}',
        ]
        if self.require_db_match:
            db_bits.append('require AR match (≥1 MATCH)')
        if self.archival_strict:
            db_bits.append('archival strict')
        if self.ar_status:
            db_bits.append(f'AR DB: {self.ar_status}')
        lines.append(' · '.join(db_bits))
        lines.append(f'CUE: {"yes" if self.write_cue_file else "no"}')
        lines.append('')

        if self.disc and self.disc.tracks:
            lines.append('TOC')
            lines.append('     Track |   Start  |  Length  |   Pregap | Start sector | End sector')
            lines.append('    ------------------------------------------------------------------')
            for t in self.disc.tracks:
                end = t.start_sector + t.length_sectors - 1
                start_lab = _msf_from_sectors(t.start_sector)
                len_lab = _msf_from_sectors(t.length_sectors)
                gap_lab = _msf_from_sectors(t.pregap_sectors)
                lines.append(
                    f'       {t.number:2d}  | {start_lab:>8s} | {len_lab:>8s} | '
                    f'{gap_lab:>8s} | {t.start_sector:9d}    | {end:8d}'
                )
            for d in getattr(self.disc, 'data_tracks', None) or []:
                end = d.start_sector + d.length_sectors - 1
                start_lab = _msf_from_sectors(d.start_sector)
                len_lab = _msf_from_sectors(d.length_sectors)
                gap_lab = _msf_from_sectors(d.pregap_sectors)
                lines.append(
                    f'       {d.number:2d}  | {start_lab:>8s} | {len_lab:>8s} | '
                    f'{gap_lab:>8s} | {d.start_sector:9d}    | {end:8d}  '
                    f'{d.cue_mode} session {d.session}'
                )
            lines.append('')

        for entry in self.tracks:
            lines.append(f'Track {entry.number:2d}')
            if entry.output_path is not None:
                lines.append(f'     Filename {entry.output_path}')
            elif entry.filename:
                lines.append(f'     Filename {entry.filename}')
            if entry.title:
                lines.append(f'     Title    {entry.title}')
            stats: list[str] = []
            if entry.peak_percent is not None:
                stats.append(f'peak {entry.peak_percent:.1f}%')
            if entry.extract_speed_x is not None:
                stats.append(f'{entry.extract_speed_x:.1f}x')
            if entry.extract_seconds is not None:
                stats.append(f'{entry.extract_seconds:.1f}s')
            quality_line = ''
            if entry.quality_percent is not None:
                quality_line = f'Track quality {entry.quality_percent:.1f} %'
            else:
                for ec in entry.error_correction_lines:
                    if ec.lower().startswith('track quality'):
                        quality_line = ec
                        break
            if quality_line:
                stats.append(quality_line.replace('Track quality ', 'quality ', 1))
            stats.append(entry.extract_mode or 'secure')
            if stats:
                lines.append('     ' + ' · '.join(stats))
            if entry.expected_wav_bytes > 0 and entry.wav_bytes > 0:
                delta = entry.wav_bytes - entry.expected_wav_bytes
                if delta != 0:
                    lines.append(
                        f'     WAV {entry.wav_bytes} B '
                        f'(expected ~{entry.expected_wav_bytes}, Δ {delta:+d})'
                    )
            # Dual-pass CRC only when a real test pass was performed.
            if entry.crc_verified is not None:
                if entry.test_crc:
                    lines.append(f'     Test CRC {entry.test_crc.upper()}')
                if entry.copy_crc:
                    lines.append(f'     Copy CRC {entry.copy_crc.upper()}')
                if entry.crc_verified:
                    lines.append('     CRCs matched')
                else:
                    lines.append('     WARNING: Test and Copy CRCs differ')
            elif entry.copy_crc:
                lines.append(f'     Audio CRC {entry.copy_crc.upper()} (single-pass)')
            if entry.accuraterip is not None:
                lines.append(f'     {_format_ar_line(entry.accuraterip)}')
            elif self.verify_accuraterip and not (
                entry.extract_mode or ''
            ).lower().startswith('raw'):
                lines.append('     AccurateRip result missing')
            # Error correction block (skip duplicate quality line).
            for ec in entry.error_correction_lines:
                if ec.lower().startswith('track quality'):
                    continue
                lines.append(f'     {ec}')
            for line in entry.attempt_log:
                lines.append(f'     Attempt: {line}')
            for note in entry.notes:
                lines.append(f'     Note: {note}')
            if entry.had_errors and entry.status.upper() in ('OK', 'FINISHED'):
                lines.append('     Copy finished with errors')
            else:
                lines.append(f'     Copy {entry.status}')
            lines.append('')

        # Summary
        ar_checked = [t for t in self.tracks if t.accuraterip is not None]
        if ar_checked:
            matches = sum(
                1
                for t in ar_checked
                if t.accuraterip and t.accuraterip.confidence == AccurateRipConfidence.MATCH
            )
            if matches == len(ar_checked):
                lines.append('All tracks accurately ripped')
            elif matches == 0:
                lines.append('No tracks could be verified as accurate')
            else:
                lines.append(f'{matches} of {len(ar_checked)} tracks accurately ripped')
            lines.append('')

        burst = [t.number for t in self.tracks if 'burst' in (t.extract_mode or '').lower()]
        if burst:
            intentional = bool(self.burst_mode) or (self.rip_mode or '').strip().lower() == 'burst'
            if intentional:
                lines.append(
                    f'Burst mode (-Z) on track(s) {", ".join(str(n) for n in burst)} '
                    '— paranoia disabled'
                )
            else:
                lines.append(
                    f'WARNING: Burst (-Z) recovery on track(s) '
                    f'{", ".join(str(n) for n in burst)} — not archival-grade secure'
                )
            lines.append('')

        crc_ok = [t.number for t in self.tracks if t.crc_verified is True]
        crc_bad = [t.number for t in self.tracks if t.crc_verified is False]
        if crc_bad:
            lines.append(
                f'Test & copy CRC MISMATCH on track(s): {", ".join(str(n) for n in crc_bad)}'
            )
            lines.append('')
        elif crc_ok:
            lines.append('Test & copy CRC matched on all dual-pass tracks')
            lines.append('')

        errored = [t.number for t in self.tracks if t.had_errors]
        if errored:
            lines.append(f'There were errors on track(s): {", ".join(str(n) for n in errored)}')
            lines.append('')

        if self.history:
            lines.append('Session history:')
            for n in self.history:
                lines.append(f'  {n}')
            lines.append('')

        if self.replaygain_notes:
            lines.append('ReplayGain:')
            for n in self.replaygain_notes:
                lines.append(f'  {n}')
            lines.append('')

        if self.notes:
            lines.append('Notes:')
            for n in self.notes:
                lines.append(f'  {n}')
            lines.append('')

        # Archival verdict first, then extraction status — avoids “No errors”
        # sitting above an Archival FAIL (or the reverse skim confusion).
        if self.outcome_archival_ok is not None:
            arch = 'OK' if self.outcome_archival_ok else 'FAIL'
            rip_mode = (self.rip_mode or '').strip().lower()
            if self.outcome_secure_ok:
                secure = 'Secure OK'
            elif self.burst_mode or rip_mode == 'burst':
                secure = 'Burst mode'
            elif rip_mode == 'normal':
                secure = 'Normal'
            else:
                secure = 'not Secure OK'
            if self.outcome_db_verified:
                db = 'DB verified'
            elif self.verify_accuraterip:
                db = 'not DB-verified'
            else:
                db = 'DB checks off'
            outcome = f'Outcome: Archival {arch} · {secure} · {db}'
            if self.outcome_summary:
                outcome = f'{outcome} ({self.outcome_summary})'
            lines.append(outcome)

        intentional_burst = bool(self.burst_mode) or (self.rip_mode or '').strip().lower() == 'burst'
        recovery_burst = bool(burst) and not intentional_burst
        if self.cancelled:
            lines.append('Extraction cancelled by user')
        elif self.error:
            lines.append(f'Errors occurred: {self.error}')
        elif any(t.had_errors for t in self.tracks) or crc_bad or recovery_burst:
            lines.append(
                'Extraction completed with corrections, burst fallback, '
                'and/or CRC issues (see per-track details)'
            )
        elif intentional_burst and burst:
            lines.append('Burst mode extract completed (paranoia disabled)')
        elif self.outcome_archival_ok is False:
            lines.append('Archival checks failed (see Outcome and notes above)')
        else:
            lines.append('No errors occurred')

        lines.append('')
        lines.append('End of status report')
        lines.append('')
        # Interchangeable log signature (Rijndael-256 CBC XOR).
        return append_eac_checksum('\n'.join(lines))

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


def analyze_wav_for_log(wav_path: Path) -> tuple[str, float | None]:
    """Return ``(crc32_hex, peak_percent)`` for a CDDA WAV file."""
    try:
        with wave.open(str(wav_path), 'rb') as wf:
            if wf.getsampwidth() != 2:
                raw = wf.readframes(wf.getnframes())
                crc = zlib.crc32(raw) & 0xFFFFFFFF
                return f'{crc:08x}', None
            raw = wf.readframes(wf.getnframes())
        return analyze_pcm_for_log(raw)
    except Exception:
        try:
            return _pcm_crc_from_raw(wav_path), None
        except OSError:
            return '', None


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


