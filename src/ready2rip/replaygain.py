# SPDX-License-Identifier: GPL-3.0-or-later
"""ReplayGain analysis via ffmpeg ebur128 (FLAC/MP3/AAC/Opus/WAV/AIFF)."""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from collections import defaultdict
from pathlib import Path

from ready2rip.rip.soundcheck import supports_sound_check
from ready2rip.tags.writer import ReplayGainValues, TagWriter

log = logging.getLogger(__name__)

# ReplayGain 2.0 / EBU R128 measurement with RG2 reference (-18 LUFS).
_REF_LUFS = -18.0

_I_RE = re.compile(r'I:\s*([+-]?\d+(?:\.\d+)?)\s*LUFS')
_PEAK_RE = re.compile(r'True peak:\s*([+-]?\d+(?:\.\d+)?)\s*dBFP?', re.IGNORECASE)
_PEAK_RE2 = re.compile(r'Peak:\s*([+-]?\d+(?:\.\d+)?)\s*dBFS', re.IGNORECASE)

# filter_complex concat argv grows with track count; beyond this fall back.
_MAX_ALBUM_CONCAT_INPUTS = 40

_AUDIO_SUFFIXES = frozenset({
    '.flac',
    '.wv',
    '.mp3',
    '.m4a',
    '.mp4',
    '.aac',
    '.opus',
    '.ogg',
    '.wav',
    '.aiff',
    '.aif',
})

# Prefer lossless/PCM for album loudness when Also-encode companions are present.
# ``.m4a`` is omitted here (ALAC vs AAC); see ``album_reference_paths``.
_PRIMARY_ALBUM_SUFFIXES = frozenset({'.flac', '.wav', '.aiff', '.aif', '.wv'})


def analyze_file(path: Path) -> ReplayGainValues | None:
    """Return track ReplayGain values for *path*, or None on failure."""
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        log.warning('ffmpeg not found; cannot analyze ReplayGain')
        return None

    cmd = [
        ffmpeg,
        '-nostats',
        '-i',
        str(path),
        '-filter_complex',
        'ebur128=peak=true',
        '-f',
        'null',
        '-',
    ]
    return _values_from_ffmpeg(cmd, label=str(path))


def analyze_album(paths: list[Path]) -> ReplayGainValues | None:
    """Whole-album R128 loudness + true peak across *paths* (concat demux)."""
    if not paths:
        return None
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        return None
    if len(paths) == 1:
        return analyze_file(paths[0])
    if len(paths) > _MAX_ALBUM_CONCAT_INPUTS:
        log.info(
            'Album ReplayGain: %s tracks — using mean of track gains '
            '(too many for one concat filter)',
            len(paths),
        )
        return None

    cmd = [ffmpeg, '-nostats']
    for path in paths:
        cmd.extend(['-i', str(path)])
    inputs = ''.join(f'[{i}:a:0]' for i in range(len(paths)))
    filt = f'{inputs}concat=n={len(paths)}:v=0:a=1,ebur128=peak=true'
    cmd.extend(['-filter_complex', filt, '-f', 'null', '-'])
    return _values_from_ffmpeg(cmd, label=f'album ({len(paths)} files)')


def album_reference_paths(paths: list[Path]) -> list[Path]:
    """Paths used for album gain (lossless/PCM when present).

    Avoids double-counting Also-encode companions (e.g. FLAC + MP3) in one
    album measurement.
    """
    if not paths:
        return []
    primary = [p for p in paths if p.suffix.lower() in _PRIMARY_ALBUM_SUFFIXES]
    if primary:
        return primary
    # ALAC lives in ``.m4a``; skip the Also-encode ``M4A/`` companion folder.
    alac = [
        p
        for p in paths
        if p.suffix.lower() == '.m4a' and p.parent.name != 'M4A'
    ]
    if alac:
        return alac
    # No lossless set — use the largest single-extension group (first-seen tie-break).
    by_suffix: dict[str, list[Path]] = defaultdict(list)
    order: list[str] = []
    for path in paths:
        suf = path.suffix.lower()
        if suf not in by_suffix:
            order.append(suf)
        by_suffix[suf].append(path)
    best_suf = max(order, key=lambda s: len(by_suffix[s]))
    return list(by_suffix[best_suf])


def apply_replaygain(
    files: list[Path],
    *,
    write_rg_tags: bool = True,
    write_sound_check: bool = False,
) -> list[str]:
    """Analyze each file once; write ReplayGain and/or iTunes Sound Check.

    Track gain uses per-file ``ebur128=peak=true``. Album gain is measured on
    the primary (lossless/PCM) set when Also-encode companions are present, then
    shared across formats. Album peak for companions is the max true peak among
    that format's tracks.

    Sound Check (iTunNORM) uses **track** gain/peak on iPod-compatible formats
    (MP3 / M4A / AIFF / WAV).
    """
    if not files:
        return []
    if not write_rg_tags and not write_sound_check:
        return []

    notes: list[str] = []
    if not shutil.which('ffmpeg'):
        notes.append('Loudness tags skipped: ffmpeg not found')
        return notes

    audio_files = [p for p in files if p.suffix.lower() in _AUDIO_SUFFIXES]
    if not audio_files:
        audio_files = list(files)

    writer = TagWriter()
    analyzed: list[tuple[Path, ReplayGainValues]] = []
    for path in audio_files:
        values = analyze_file(path)
        if values is None:
            notes.append(f'Loudness analysis skipped for {path.name}')
            continue
        analyzed.append((path, values))

    if not analyzed:
        notes.append('Loudness tags: no tracks analyzed')
        return notes

    all_paths = [p for p, _v in analyzed]
    ref_paths = album_reference_paths(all_paths)
    ref_set = set(ref_paths)
    ref_analyzed = [(p, v) for p, v in analyzed if p in ref_set]
    if not ref_analyzed:
        ref_analyzed = analyzed
        ref_paths = all_paths
        ref_set = set(ref_paths)

    album_vals = analyze_album(ref_paths)
    if album_vals is not None:
        album_gain = album_vals.track_gain_db
        ref_album_peak = album_vals.track_peak
        album_method = f'whole-album ebur128 on {len(ref_paths)} primary file(s)'
    else:
        album_gain = sum(v.track_gain_db for _p, v in ref_analyzed) / len(ref_analyzed)
        ref_album_peak = max(v.track_peak for _p, v in ref_analyzed)
        album_method = (
            f'mean of {len(ref_analyzed)} primary track gain(s) (fallback)'
        )

    peaks_by_suffix: dict[str, float] = defaultdict(float)
    for path, values in analyzed:
        suf = path.suffix.lower()
        peaks_by_suffix[suf] = max(peaks_by_suffix[suf], values.track_peak)

    sc_written = 0
    for path, values in analyzed:
        values.album_gain_db = album_gain
        suf = path.suffix.lower()
        if path in ref_set:
            values.album_peak = ref_album_peak
        else:
            values.album_peak = peaks_by_suffix.get(suf, values.track_peak)
        if write_rg_tags:
            writer.write_replaygain(path, values)
        if write_sound_check and supports_sound_check(path.suffix):
            if writer.write_sound_check(
                path,
                gain_db=values.track_gain_db,
                peak=values.track_peak,
            ):
                sc_written += 1

    if write_rg_tags:
        notes.append(
            f'ReplayGain track + album (ffmpeg ebur128 true peak, {album_method}) on '
            f'{len(analyzed)} file(s) '
            f'(album gain {album_gain:+.2f} dB, album peak {ref_album_peak:.6f})'
        )
    if write_sound_check:
        notes.append(
            f'iTunes Sound Check (iTunNORM from track ReplayGain) on '
            f'{sc_written} iPod-compatible file(s)'
        )
    return notes


def _values_from_ffmpeg(cmd: list[str], *, label: str) -> ReplayGainValues | None:
    try:
        completed = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=max(600, 120 * max(1, cmd.count('-i'))),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.warning('ReplayGain analysis failed for %s: %s', label, exc)
        return None

    text = (completed.stderr or '') + '\n' + (completed.stdout or '')
    integrated = None
    peak_db = None
    for match in _I_RE.finditer(text):
        integrated = float(match.group(1))
    for match in _PEAK_RE.finditer(text):
        peak_db = float(match.group(1))
    if peak_db is None:
        for match in _PEAK_RE2.finditer(text):
            peak_db = float(match.group(1))

    if integrated is None:
        log.warning('Could not parse LUFS from ffmpeg for %s', label)
        return None

    gain = _REF_LUFS - integrated
    if peak_db is None:
        peak_linear = 1.0
    else:
        peak_linear = 10.0 ** (peak_db / 20.0)

    return ReplayGainValues(
        track_gain_db=gain,
        track_peak=peak_linear,
    )
