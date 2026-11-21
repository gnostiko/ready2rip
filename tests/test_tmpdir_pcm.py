# SPDX-License-Identifier: GPL-3.0-or-later
"""Scratch dir preference and CDDA WAV mmap slicing."""

from __future__ import annotations

import wave
from pathlib import Path

from ready2rip.rip.pcm_io import (
    image_track_pcm,
    image_track_pcm_from_view,
    open_cdda_wav_mmap,
)
from ready2rip.rip.tmpdir import preferred_scratch_parent, rip_scratch_dir


def _write_cdda_wav(path: Path, nframes: int) -> None:
    # Silence PCM (zeros).
    pcm = b'\x00\x00' * 2 * nframes
    with wave.open(str(path), 'wb') as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(44100)
        wf.writeframes(pcm)


def test_preferred_scratch_uses_output(tmp_path: Path) -> None:
    out = tmp_path / 'Music'
    out.mkdir()
    parent = preferred_scratch_parent(out)
    assert parent == out / '.ready2rip-tmp'
    assert parent.is_dir()


def test_rip_scratch_dir_cleans_up(tmp_path: Path) -> None:
    with rip_scratch_dir(tmp_path) as scratch:
        assert scratch.is_dir()
        (scratch / 'x').write_text('ok')
    assert not scratch.exists()


def test_mmap_slice_matches_bytes(tmp_path: Path) -> None:
    # 10 sectors of audio (10 * 588 frames).
    nframes = 10 * 588
    wav = tmp_path / 'img.wav'
    _write_cdda_wav(wav, nframes)
    # Mark a non-zero pattern in sector 2 so slices differ.
    with open(wav, 'r+b') as fh:
        # Standard PCM data at offset 44.
        fh.seek(44 + 2 * 2352)
        fh.write(b'\x11' * 2352)

    with open_cdda_wav_mmap(wav) as view:
        sliced = image_track_pcm_from_view(
            view, start_sector=2, length_sectors=1, file_start=0
        )
        full = image_track_pcm(
            memoryview(view.mm)[view.pcm_offset : view.pcm_offset + view.pcm_nbytes],
            start_sector=2,
            length_sectors=1,
            file_start=0,
        )
    assert len(sliced) == 2352
    assert sliced == full
    assert sliced == b'\x11' * 2352
