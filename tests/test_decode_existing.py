# SPDX-License-Identifier: GPL-3.0-or-later
"""Decode existing lossless files for skip-existing re-verify."""

from __future__ import annotations

import wave
from pathlib import Path

from ready2rip.rip.engine import RipEngine


def _write_silence_wav(path: Path, frames: int = 4410) -> None:
    with wave.open(str(path), 'wb') as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(44100)
        wf.writeframes(b'\x00\x00' * 2 * frames)


def test_decode_existing_wav_copies(tmp_path: Path) -> None:
    src = tmp_path / 'a.wav'
    dest = tmp_path / 'b.wav'
    _write_silence_wav(src)
    assert RipEngine._decode_existing_to_wav(src, dest)
    assert dest.is_file()
    assert dest.stat().st_size == src.stat().st_size


def test_decode_existing_rejects_lossy(tmp_path: Path) -> None:
    src = tmp_path / 'a.mp3'
    src.write_bytes(b'ID3fake')
    dest = tmp_path / 'b.wav'
    assert not RipEngine._decode_existing_to_wav(src, dest)
