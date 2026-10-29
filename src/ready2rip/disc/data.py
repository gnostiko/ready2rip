# SPDX-License-Identifier: GPL-3.0-or-later
"""Raw 2352-byte data-track extraction (mixed-mode / CD-Extra)."""

from __future__ import annotations

import logging
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ready2rip.disc.mmc import (
    MmcDevice,
    detect_sector_mode,
)
from ready2rip.disc.probe import DataTrackInfo

log = logging.getLogger(__name__)

_BATCH = 32
_SECTOR = 2352
_RETRIES = 8

ProgressFn = Callable[[int, int, int, str], None]
CancelledFn = Callable[[], bool]


@dataclass
class DataExtractResult:
    """One extracted data track (.bin)."""

    track: DataTrackInfo
    path: Path
    crc32: str
    retries: int = 0
    message: str = ''


def extract_data_tracks(
    device: str,
    tracks: list[DataTrackInfo],
    dest_dir: Path,
    *,
    on_progress: ProgressFn | None = None,
    cancelled: CancelledFn | None = None,
) -> list[DataExtractResult]:
    """Write raw MODE1/2352 or MODE2/2352 ``trackNN.bin`` files.

    No sample offset is applied. Failed sectors are retried individually.
    Raises ``RuntimeError`` if a track cannot be read completely.
    """
    if not tracks:
        return []
    dest_dir.mkdir(parents=True, exist_ok=True)
    results: list[DataExtractResult] = []
    try:
        mmc = MmcDevice(device, nonblock=False)
    except OSError as exc:
        raise RuntimeError(f'Cannot open {device} for data-track extract: {exc}') from exc
    try:
        for track in tracks:
            if cancelled is not None and cancelled():
                raise RuntimeError('Cancelled')
            results.append(
                _extract_one(
                    mmc,
                    track,
                    dest_dir,
                    on_progress=on_progress,
                    cancelled=cancelled,
                )
            )
    finally:
        mmc.close()
    return results


def data_bin_name(number: int) -> str:
    return f'track{int(number):02d}.bin'


def _extract_one(
    mmc: MmcDevice,
    track: DataTrackInfo,
    dest_dir: Path,
    *,
    on_progress: ProgressFn | None,
    cancelled: CancelledFn | None,
) -> DataExtractResult:
    path = dest_dir / data_bin_name(track.number)
    length = int(track.length_sectors)
    start = int(track.start_sector)
    if length <= 0:
        raise RuntimeError(f'Data track {track.number} has no sectors')

    mode = int(track.mode) if track.mode in (1, 2) else 0
    retries = 0
    crc = 0
    if on_progress is not None:
        on_progress(
            track.number,
            0,
            length,
            f'Extracting data track {track.number:02d} ({track.cue_mode})…',
        )

    with path.open('wb') as fh:
        offset = 0
        while offset < length:
            if cancelled is not None and cancelled():
                raise RuntimeError('Cancelled')
            n = min(_BATCH, length - offset)
            lba = start + offset
            chunk = mmc.read_raw_sectors(lba, n)
            if chunk is None or len(chunk) != _SECTOR * n:
                chunk, extra = _read_sectors_slow(mmc, lba, n)
                retries += extra
            if mode == 0 and offset == 0 and len(chunk) >= _SECTOR:
                mode = detect_sector_mode(chunk[:_SECTOR])
            fh.write(chunk)
            crc = zlib.crc32(chunk, crc)
            offset += n
            if on_progress is not None and (offset == length or offset % 512 == 0):
                on_progress(
                    track.number,
                    offset,
                    length,
                    f'Data track {track.number:02d}: {offset}/{length} sectors',
                )

    size = path.stat().st_size
    expected = length * _SECTOR
    if size != expected:
        raise RuntimeError(
            f'Data track {track.number} size {size} != expected {expected}'
        )
    final_mode = mode if mode in (1, 2) else 1
    out_track = DataTrackInfo(
        number=track.number,
        start_sector=track.start_sector,
        length_sectors=track.length_sectors,
        mode=final_mode,
        session=track.session,
        pregap_sectors=track.pregap_sectors,
    )
    crc_hex = f'{crc & 0xFFFFFFFF:08x}'
    msg = (
        f'Data track {track.number:02d} {out_track.cue_mode} '
        f'{length} sectors → {path.name} CRC32 {crc_hex}'
    )
    log.info('%s', msg)
    return DataExtractResult(
        track=out_track,
        path=path,
        crc32=crc_hex,
        retries=retries,
        message=msg,
    )


def _read_sectors_slow(mmc: MmcDevice, lba: int, count: int) -> tuple[bytes, int]:
    """Single-sector retries when a batched READ CD fails."""
    parts: list[bytes] = []
    retries = 0
    for i in range(count):
        sector = mmc.read_raw_sector(lba + i, retries=_RETRIES)
        if sector is None or len(sector) < _SECTOR:
            raise RuntimeError(
                f'Failed to read data sector LBA {lba + i} after {_RETRIES} retries'
            )
        retries += 1
        parts.append(sector[:_SECTOR])
    return b''.join(parts), retries
