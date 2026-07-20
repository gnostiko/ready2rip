# SPDX-License-Identifier: GPL-3.0-or-later
"""ReplayGain analysis via ffmpeg ebur128 (works for FLAC/MP3/Opus/WAV)."""

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
    try:
        completed = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=600,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.warning('ReplayGain analysis failed for %s: %s', path, exc)
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
        log.warning('Could not parse LUFS from ffmpeg for %s', path)
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


def apply_replaygain(files: list[Path]) -> list[str]:
    """Analyze each file once; write track + album ReplayGain tags.

    Always uses ffmpeg ``ebur128=peak=true`` (R128 loudness + true peak with
    libebur128’s 4× oversampling). metaflac is not used, so FLAC matches other
    formats and gets true-peak tags instead of sample peaks.
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

    album_gain = sum(v.track_gain_db for _p, v in analyzed) / len(analyzed)
    album_peak = max(v.track_peak for _p, v in analyzed)
    for path, values in analyzed:
        values.album_gain_db = album_gain
        values.album_peak = album_peak
        writer.write_replaygain(path, values)

    notes.append(
        f'ReplayGain track + album (ffmpeg ebur128 true peak) on '
        f'{len(analyzed)} file(s) '
        f'(album gain {album_gain:+.2f} dB, album peak {album_peak:.6f})'
    )
    return notes
