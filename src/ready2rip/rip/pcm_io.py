# SPDX-License-Identifier: GPL-3.0-or-later
"""CDDA WAV helpers: load, mmap, and TOC-slice without full-disc copies."""

from __future__ import annotations

import mmap
import struct
import wave
from dataclasses import dataclass
from pathlib import Path


@dataclass
class CddaWavView:
    """Memory-mapped 16-bit stereo 44.1 kHz WAV PCM region."""

    path: Path
    mm: mmap.mmap
    pcm_offset: int
    nframes: int
    _fh: object = None

    @property
    def pcm_nbytes(self) -> int:
        return int(self.nframes) * 4

    def close(self) -> None:
        try:
            self.mm.close()
        except BufferError:
            pass
        fh = self._fh
        self._fh = None
        if fh is not None:
            try:
                fh.close()
            except OSError:
                pass

    def __enter__(self) -> CddaWavView:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _validate_cdda_wav(wf: wave.Wave_read) -> None:
    if wf.getnchannels() != 2 or wf.getsampwidth() != 2 or wf.getframerate() != 44100:
        raise ValueError(
            f'CDDA WAV requires 16-bit stereo 44.1 kHz, got '
            f'{wf.getnchannels()}ch {wf.getsampwidth() * 8}bit {wf.getframerate()}Hz'
        )


def _find_wav_data_offset(path: Path) -> int:
    """Byte offset of PCM samples (after the ``data`` chunk header)."""
    with open(path, 'rb') as fh:
        header = fh.read(12)
        if len(header) < 12 or header[0:4] != b'RIFF' or header[8:12] != b'WAVE':
            raise ValueError(f'Not a RIFF/WAVE file: {path}')
        while True:
            hdr = fh.read(8)
            if len(hdr) < 8:
                raise ValueError(f'WAV missing data chunk: {path}')
            chunk_id, chunk_size = struct.unpack('<4sI', hdr)
            if chunk_id == b'data':
                return fh.tell()
            # Chunk sizes are word-aligned.
            skip = int(chunk_size) + (int(chunk_size) & 1)
            fh.seek(skip, 1)


def load_cdda_pcm(path: Path) -> bytes:
    """Load raw interleaved LE PCM from a 16-bit stereo 44.1 kHz WAV."""
    with wave.open(str(path), 'rb') as wf:
        _validate_cdda_wav(wf)
        return wf.readframes(wf.getnframes())


def open_cdda_wav_mmap(path: Path) -> CddaWavView:
    """mmap a CDDA WAV; PCM starts at *pcm_offset* for *nframes* frames."""
    with wave.open(str(path), 'rb') as wf:
        _validate_cdda_wav(wf)
        nframes = int(wf.getnframes())
    pcm_offset = _find_wav_data_offset(path)
    # Keep the fd open for the lifetime of the mmap (closed in CddaWavView.close).
    fh = open(path, 'rb')  # noqa: SIM115
    try:
        mm = mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ)
        end = pcm_offset + nframes * 4
        if end > len(mm):
            raise ValueError(
                f'WAV PCM extends past file end ({end} > {len(mm)}): {path}'
            )
    except Exception:
        fh.close()
        raise
    return CddaWavView(
        path=path, mm=mm, pcm_offset=pcm_offset, nframes=nframes, _fh=fh
    )


def image_track_pcm(
    image_pcm: bytes | memoryview,
    *,
    start_sector: int,
    length_sectors: int,
    file_start: int,
) -> bytes:
    """Slice one TOC track's CDDA bytes out of a continuous image PCM blob."""
    rel = max(0, int(start_sector) - int(file_start))
    start = rel * 2352
    length = max(0, int(length_sectors)) * 2352
    nbytes = len(image_pcm)
    if length <= 0 or start >= nbytes:
        return b''
    return bytes(image_pcm[start : min(start + length, nbytes)])


def image_track_pcm_from_view(
    view: CddaWavView,
    *,
    start_sector: int,
    length_sectors: int,
    file_start: int,
) -> bytes:
    """Slice a track from a mmap'd image without loading the whole disc."""
    end = view.pcm_offset + view.pcm_nbytes
    region = memoryview(view.mm)[view.pcm_offset : end]
    return image_track_pcm(
        region,
        start_sector=start_sector,
        length_sectors=length_sectors,
        file_start=file_start,
    )
