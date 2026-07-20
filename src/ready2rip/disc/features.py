# SPDX-License-Identifier: GPL-3.0-or-later
"""Drive feature tests: Accurate Stream and C2 error pointers (EAC-style)."""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
import wave
import zlib
from dataclasses import dataclass
from pathlib import Path

from ready2rip.disc.cache import flush_drive_cache
from ready2rip.disc.probe import DiscInfo

log = logging.getLogger(__name__)

# ~1 s of CDDA — long enough to catch positioning jitter, short enough to be fast.
_STREAM_SECTORS = 75
_STREAM_TRIALS = 2


@dataclass
class FeatureTestResult:
    """Result of a single drive-feature probe."""

    supported: bool | None  # True / False / None (unknown)
    message: str
    detail: str = ''


def test_accurate_stream(
    device: str,
    disc: DiscInfo | None = None,
    *,
    trials: int = _STREAM_TRIALS,
    timeout: int = 120,
) -> FeatureTestResult:
    """Test whether the drive re-reads the same LBA with identical samples.

    EAC “Accurate Stream”: after seeking away and returning, the drive
    delivers the same PCM for a given address. Without it, re-reads can
    shift by a few samples (jitter), which is why overlap/paranoia is needed.

    Method:
      1. Burst-extract a short absolute sector span.
      2. Flush / seek away (distant read).
      3. Extract the same span again.
      4. Compare PCM CRC. Repeat *trials* times.
    All pairs must match for a positive result.
    """
    if shutil.which('cdparanoia') is None:
        return FeatureTestResult(
            supported=None,
            message='cdparanoia not found; Accurate Stream not tested',
        )

    span = _stream_span(disc)
    if span is None:
        return FeatureTestResult(
            supported=None,
            message='No usable sector range for Accurate Stream test',
        )

    matches = 0
    mismatches = 0
    errors = 0
    last_detail = ''

    with tempfile.TemporaryDirectory(prefix='ready2rip-astream-') as tmp:
        tmp_path = Path(tmp)
        for trial in range(max(1, trials)):
            a = tmp_path / f'a{trial}.wav'
            b = tmp_path / f'b{trial}.wav'
            try:
                _burst_span(device, span, a, timeout=timeout)
                crc_a = _pcm_crc(a)
                flush_drive_cache(device, disc, timeout=min(60, timeout))
                _burst_span(device, span, b, timeout=timeout)
                crc_b = _pcm_crc(b)
            except Exception as exc:  # noqa: BLE001
                errors += 1
                last_detail = str(exc)
                log.warning('Accurate Stream trial %s failed: %s', trial + 1, exc)
                continue

            if crc_a is None or crc_b is None or crc_a == 0:
                errors += 1
                last_detail = 'Could not hash extracted audio'
                continue

            if crc_a == crc_b:
                matches += 1
            else:
                mismatches += 1
                last_detail = f'CRC mismatch {crc_a:08x} vs {crc_b:08x}'

            try:
                a.unlink(missing_ok=True)
                b.unlink(missing_ok=True)
            except OSError:
                pass

    total = matches + mismatches
    if total == 0:
        return FeatureTestResult(
            supported=None,
            message=f'Accurate Stream test failed ({errors} error(s))',
            detail=last_detail,
        )

    if mismatches == 0 and matches >= 1:
        return FeatureTestResult(
            supported=True,
            message=(
                f'Accurate Stream: yes '
                f'({matches}/{total} re-read pair(s) matched after seek)'
            ),
            detail=f'span={span}',
        )

    return FeatureTestResult(
        supported=False,
        message=(
            f'Accurate Stream: no '
            f'({mismatches} mismatch(es), {matches} match(es) of {total})'
        ),
        detail=last_detail or f'span={span}',
    )


def test_c2_pointers(device: str = '/dev/sr0', *, timeout: int = 15) -> FeatureTestResult:
    """Detect C2 error-pointer support via MMC feature reporting (cd-drive).

    EAC “C2 pointers”: the drive can flag uncorrectable sample errors while
    reading CDDA. Linux user-space rippers rarely *consume* C2 data during
    extraction (cdparanoia does not), but knowing support is still useful for
    logs and drive capability tables.

    Primary source: ``cd-drive`` “CD Read Feature / C2 Error pointers…”.
    """
    # Prefer libcdio's cd-drive (already on Solus with the user).
    cd_drive = shutil.which('cd-drive')
    if cd_drive:
        try:
            completed = subprocess.run(
                [cd_drive, '-q', device],
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            log.warning('cd-drive C2 probe failed: %s', exc)
            completed = None

        if completed is not None:
            text = (completed.stdout or '') + '\n' + (completed.stderr or '')
            result = _parse_c2_from_cd_drive(text)
            if result is not None:
                return result

    # Fallback: look at udev/sysfs capability flags (rarely present for C2).
    return FeatureTestResult(
        supported=None,
        message=(
            'C2 Error pointers: unknown '
            '(install cd-drive / libcdio-utils for MMC feature reporting)'
        ),
    )


def _parse_c2_from_cd_drive(text: str) -> FeatureTestResult | None:
    """Parse cd-drive output for CD Read Feature C2 support."""
    if not text.strip():
        return None

    # Strong positive: "C2 Error pointers are supported"
    if re.search(r'C2\s+Error\s+pointers?\s+are\s+supported', text, re.I):
        return FeatureTestResult(
            supported=True,
            message='C2 Error pointers: yes (MMC CD Read Feature)',
            detail='Reported by cd-drive',
        )

    # Explicit negative variants
    if re.search(
        r'C2\s+Error\s+pointers?\s+are\s+not\s+supported',
        text,
        re.I,
    ):
        return FeatureTestResult(
            supported=False,
            message='C2 Error pointers: no (MMC CD Read Feature)',
            detail='Reported by cd-drive',
        )

    # If CD Read Feature section exists but no C2 line → treat as unsupported
    if re.search(r'CD\s+Read\s+Feature', text, re.I):
        # Only conclude "no" when we clearly saw the feature block without C2.
        # Some older cd-drive builds omit the line when unsupported.
        if not re.search(r'C2\s+Error', text, re.I):
            return FeatureTestResult(
                supported=False,
                message='C2 Error pointers: no (not listed in CD Read Feature)',
                detail='CD Read Feature present without C2 line',
            )

    return FeatureTestResult(
        supported=None,
        message='C2 Error pointers: unknown (could not parse cd-drive output)',
    )


def _stream_span(disc: DiscInfo | None) -> str | None:
    if disc is None or not disc.tracks:
        return f'[.0]-[.{_STREAM_SECTORS}]'

    # Prefer a mid-disc track that is long enough (avoid track 1 HTOA edge cases).
    candidates = sorted(
        disc.tracks,
        key=lambda t: (0 if t.length_sectors >= _STREAM_SECTORS + 10 else 1, t.number),
    )
    track = candidates[0]
    start = track.start_sector + max(
        0, min(track.length_sectors // 3, track.length_sectors - _STREAM_SECTORS - 1)
    )
    end = start + _STREAM_SECTORS
    if end <= start:
        end = start + _STREAM_SECTORS
    return f'[.{start}]-[.{end}]'


def _burst_span(device: str, span: str, wav_path: Path, *, timeout: int) -> None:
    cmd = [
        'cdparanoia',
        '-w',
        '-d',
        device,
        '-Z',  # burst — raw re-read without paranoia re-sync masking jitter
        '-q',
        span,
        str(wav_path),
    ]
    completed = subprocess.run(
        cmd,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if not wav_path.is_file() or wav_path.stat().st_size < 1000:
        detail = (completed.stderr or completed.stdout or '').strip()
        if len(detail) > 200:
            detail = detail[:200] + '…'
        raise RuntimeError(detail or f'cdparanoia burst span failed ({span})')


def _pcm_crc(wav_path: Path) -> int | None:
    try:
        with wave.open(str(wav_path), 'rb') as wf:
            crc = 0
            remaining = wf.getnframes()
            while remaining > 0:
                take = min(65536, remaining)
                data = wf.readframes(take)
                remaining -= take
                crc = zlib.crc32(data, crc)
            return crc & 0xFFFFFFFF
    except Exception:  # noqa: BLE001
        try:
            raw = wav_path.read_bytes()
            pcm = raw[44:] if len(raw) > 44 and raw[:4] == b'RIFF' else raw
            return zlib.crc32(pcm) & 0xFFFFFFFF
        except OSError:
            return None
