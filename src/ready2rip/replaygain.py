# SPDX-License-Identifier: GPL-3.0-or-later
"""ReplayGain analysis via ffmpeg ebur128 (FLAC/MP3/AAC/Opus/WAV)."""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from pathlib import Path

from ready2rip.tags.writer import ReplayGainValues, TagWriter

log = logging.getLogger(__name__)

# ReplayGain 2.0 / EBU R128 reference level
_REF_LUFS = -18.0

_I_RE = re.compile(r'I:\s*([+-]?\d+(?:\.\d+)?)\s*LUFS')
_PEAK_RE = re.compile(r'True peak:\s*([+-]?\d+(?:\.\d+)?)\s*dBFP?', re.IGNORECASE)
_PEAK_RE2 = re.compile(r'Peak:\s*([+-]?\d+(?:\.\d+)?)\s*dBFS', re.IGNORECASE)

# filter_complex concat argv grows with track count; beyond this fall back.
_MAX_ALBUM_CONCAT_INPUTS = 40


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


def apply_replaygain(files: list[Path]) -> list[str]:
    """Analyze each file once; write track + album ReplayGain tags.

    Track gain uses per-file ``ebur128=peak=true``. Album gain is a single
    R128 measurement over the concatenated album when possible; otherwise the
    mean of track gains (with a log note).
    """
    if not files:
        return []

    notes: list[str] = []
    if not shutil.which('ffmpeg'):
        notes.append('ReplayGain skipped: ffmpeg not found')
        return notes

    writer = TagWriter()
    analyzed: list[tuple[Path, ReplayGainValues]] = []
    for path in files:
        values = analyze_file(path)
        if values is None:
            notes.append(f'ReplayGain skipped for {path.name}')
            continue
        analyzed.append((path, values))

    if not analyzed:
        notes.append('ReplayGain: no tracks analyzed')
        return notes

    album_paths = [p for p, _v in analyzed]
    album_vals = analyze_album(album_paths)
    if album_vals is not None:
        album_gain = album_vals.track_gain_db
        album_peak = album_vals.track_peak
        album_method = 'whole-album ebur128'
    else:
        album_gain = sum(v.track_gain_db for _p, v in analyzed) / len(analyzed)
        album_peak = max(v.track_peak for _p, v in analyzed)
        album_method = 'mean of track gains (fallback)'

    for path, values in analyzed:
        values.album_gain_db = album_gain
        values.album_peak = album_peak
        writer.write_replaygain(path, values)

    notes.append(
        f'ReplayGain track + album (ffmpeg ebur128 true peak, {album_method}) on '
        f'{len(analyzed)} file(s) '
        f'(album gain {album_gain:+.2f} dB, album peak {album_peak:.6f})'
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
