# SPDX-License-Identifier: GPL-3.0-or-later
"""ReplayGain helpers (no ffmpeg / PyGObject required)."""

from __future__ import annotations

from pathlib import Path

from ready2rip.replaygain import album_reference_paths
from ready2rip.tags.writer import rg2_db_to_r128_q78


def test_album_reference_paths_prefers_lossless() -> None:
    files = [
        Path('/out/FLAC/01.flac'),
        Path('/out/FLAC/02.flac'),
        Path('/out/MP3/01.mp3'),
        Path('/out/MP3/02.mp3'),
    ]
    assert album_reference_paths(files) == [
        Path('/out/FLAC/01.flac'),
        Path('/out/FLAC/02.flac'),
    ]


def test_album_reference_paths_lossy_only_largest_group() -> None:
    files = [
        Path('/out/a.mp3'),
        Path('/out/b.mp3'),
        Path('/out/c.opus'),
    ]
    assert album_reference_paths(files) == [
        Path('/out/a.mp3'),
        Path('/out/b.mp3'),
    ]


def test_rg2_to_r128_q78_offset() -> None:
    # 0 dB @ −18 LUFS → −5 dB @ −23 → −1280 in Q7.8.
    assert rg2_db_to_r128_q78(0.0) == -1280
    # +5 dB RG2 → 0 dB R128.
    assert rg2_db_to_r128_q78(5.0) == 0
    # −1.74 dB RG2 → −6.74 dB R128 → round(-6.74 * 256) = −1725.
    assert rg2_db_to_r128_q78(-1.74) == -1725
