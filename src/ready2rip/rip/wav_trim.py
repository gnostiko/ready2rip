# SPDX-License-Identifier: GPL-3.0-or-later
"""In-place CDDA WAV length trim (gi-free)."""

from __future__ import annotations

import logging
import struct
from pathlib import Path

log = logging.getLogger(__name__)


def trim_wav_to_expected(path: Path, expected_bytes: int) -> None:
    """Crop an over-long CDDA WAV back to the TOC-expected byte size.

    Prefer an in-place RIFF header rewrite + truncate so full-disc images are
    not re-read into RAM (that freeze looked like a stuck progress bar).
    """
    expected = int(expected_bytes)
    if expected <= 44 or not path.is_file():
        return
    try:
        size = path.stat().st_size
    except OSError:
        return
    if size <= expected:
        return
    pcm_bytes = expected - 44
    if pcm_bytes <= 0:
        return
    try:
        with path.open('r+b') as handle:
            header = handle.read(44)
            if (
                len(header) == 44
                and header[0:4] == b'RIFF'
                and header[8:12] == b'WAVE'
                and header[36:40] == b'data'
            ):
                handle.seek(4)
                handle.write(struct.pack('<I', expected - 8))
                handle.seek(40)
                handle.write(struct.pack('<I', pcm_bytes))
                handle.truncate(expected)
                return
        # Non-standard header: stream-copy the TOC-sized prefix.
        tmp = path.with_suffix(path.suffix + '.trim')
        with path.open('rb') as src, tmp.open('wb') as dst:
            remaining = expected
            while remaining > 0:
                chunk = src.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                dst.write(chunk)
                remaining -= len(chunk)
        tmp.replace(path)
    except Exception as exc:
        log.warning('WAV trim failed for %s: %s', path, exc)
