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
# Match audio secure test&copy retry budget (avoid importing rip.secure → cycle).
_DEFAULT_TEST_COPY_RETRIES = 3

ProgressFn = Callable[[int, int, int, str], None]
CancelledFn = Callable[[], bool]


@dataclass
class DataExtractResult:
    """One extracted data track (.bin)."""

    track: DataTrackInfo
    path: Path
    crc32: str
    test_crc: str = ''
    retries: int = 0
    matched: bool = True
    message: str = ''


def extract_data_tracks(
    device: str,
    tracks: list[DataTrackInfo],
    dest_dir: Path,
    *,
    on_progress: ProgressFn | None = None,
    cancelled: CancelledFn | None = None,
    test_and_copy: bool = True,
    max_retries: int = _DEFAULT_TEST_COPY_RETRIES,
    flush: Callable[[], None] | None = None,
) -> list[DataExtractResult]:
    """Write raw MODE1/2352 or MODE2/2352 ``trackNN.bin`` files.

    No sample offset is applied. Failed sectors are retried individually.
    When *test_and_copy* is True, extract twice and require matching CRC32
    (with *max_retries* extra cycles after a mismatch). Raises ``RuntimeError``
    if a track cannot be read completely or CRCs never match.
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
                    test_and_copy=test_and_copy,
                    max_retries=max_retries,
                    flush=flush,
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
    test_and_copy: bool,
    max_retries: int,
    flush: Callable[[], None] | None,
) -> DataExtractResult:
    path = dest_dir / data_bin_name(track.number)
    test_path = dest_dir / f'.{data_bin_name(track.number)}.test'
    length = int(track.length_sectors)
    if length <= 0:
        raise RuntimeError(f'Data track {track.number} has no sectors')

    attempts = 1 + max(0, int(max_retries)) if test_and_copy else 1
    last_err = ''

    for attempt in range(attempts):
        if cancelled is not None and cancelled():
            raise RuntimeError('Cancelled')
        label = (
            f'Data track {track.number:02d}'
            + (f' (retry {attempt})' if attempt else '')
        )
        if test_and_copy:
            test_res = _read_to_file(
                mmc,
                track,
                test_path,
                on_progress=on_progress,
                cancelled=cancelled,
                phase=f'{label} test',
            )
            if flush is not None:
                try:
                    flush()
                except Exception as exc:
                    log.debug('Data-track cache flush failed: %s', exc)
            copy_res = _read_to_file(
                mmc,
                track,
                path,
                on_progress=on_progress,
                cancelled=cancelled,
                phase=f'{label} copy',
            )
            try:
                test_path.unlink(missing_ok=True)
            except OSError:
                pass
            if test_res.crc32 and copy_res.crc32 and test_res.crc32 == copy_res.crc32:
                msg = (
                    f'Data track {track.number:02d} {copy_res.track.cue_mode} '
                    f'{length} sectors → {path.name} '
                    f'test&copy CRC32 {copy_res.crc32}'
                )
                log.info('%s', msg)
                return DataExtractResult(
                    track=copy_res.track,
                    path=path,
                    crc32=copy_res.crc32,
                    test_crc=test_res.crc32,
                    retries=test_res.retries + copy_res.retries,
                    matched=True,
                    message=msg,
                )
            last_err = (
                f'Data track {track.number:02d} test/copy CRC mismatch '
                f'({test_res.crc32} ≠ {copy_res.crc32})'
            )
            log.warning('%s', last_err)
            continue

        single = _read_to_file(
            mmc,
            track,
            path,
            on_progress=on_progress,
            cancelled=cancelled,
            phase=label,
        )
        msg = (
            f'Data track {track.number:02d} {single.track.cue_mode} '
            f'{length} sectors → {path.name} CRC32 {single.crc32}'
        )
        log.info('%s', msg)
        return DataExtractResult(
            track=single.track,
            path=path,
            crc32=single.crc32,
            retries=single.retries,
            matched=True,
            message=msg,
        )

    try:
        test_path.unlink(missing_ok=True)
    except OSError:
        pass
    raise RuntimeError(last_err or f'Data track {track.number} extract failed')


@dataclass
class _PassResult:
    track: DataTrackInfo
    crc32: str
    retries: int


def _read_to_file(
    mmc: MmcDevice,
    track: DataTrackInfo,
    path: Path,
    *,
    on_progress: ProgressFn | None,
    cancelled: CancelledFn | None,
    phase: str,
) -> _PassResult:
    length = int(track.length_sectors)
    start = int(track.start_sector)
    mode = int(track.mode) if track.mode in (1, 2) else 0
    retries = 0
    crc = 0
    if on_progress is not None:
        on_progress(track.number, 0, length, f'{phase}…')

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
                    f'{phase}: {offset}/{length} sectors',
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
    return _PassResult(
        track=out_track,
        crc32=f'{crc & 0xFFFFFFFF:08x}',
        retries=retries,
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
