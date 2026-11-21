# SPDX-License-Identifier: GPL-3.0-or-later
"""Rip mode migration, paranoia flags, and extract-path wiring."""

from __future__ import annotations

from ready2rip.rip.engine import RipEngine
from ready2rip.rip.engine_types import RipJob
from ready2rip.rip.job_extract import job_extract_mode, sync_job_extract_flags
from ready2rip.rip.secure import (
    burst_paranoia_flags,
    normal_paranoia_flags,
    paranoia_flags_for_mode,
    secure_paranoia_flags,
)
from ready2rip.settings_defs import (
    derive_extract_flags,
    migrate_rip_mode_from_legacy,
    normalize_rip_mode,
    rip_mode_subtitle,
    rip_mode_tooltip,
)


def test_normalize_rip_mode() -> None:
    assert normalize_rip_mode('ARCHIVAL') == 'archival'
    assert normalize_rip_mode('nope') == 'archival'


def test_rip_mode_subtitle() -> None:
    assert 'secure ok' in rip_mode_subtitle('archival').lower()
    assert 'not secure ok' in rip_mode_subtitle('normal').lower()
    assert 'not secure ok' in rip_mode_subtitle('burst').lower()
    assert '-z' in rip_mode_subtitle('burst').lower()
    assert rip_mode_subtitle('nope') == rip_mode_subtitle('archival')


def test_rip_mode_tooltip_distinguishes_secure_ok() -> None:
    tip = rip_mode_tooltip().lower()
    assert 'secure ok' in tip
    assert 'never secure ok' in tip
    assert 'db verified' in tip
    assert 'archival' in tip and 'normal' in tip and 'burst' in tip


def test_migrate_from_legacy_bools() -> None:
    assert (
        migrate_rip_mode_from_legacy(burst_mode=True, test_and_copy=True) == 'burst'
    )
    assert (
        migrate_rip_mode_from_legacy(burst_mode=False, test_and_copy=True)
        == 'archival'
    )
    assert (
        migrate_rip_mode_from_legacy(burst_mode=False, test_and_copy=False)
        == 'normal'
    )
    assert (
        migrate_rip_mode_from_legacy(
            burst_mode=True, test_and_copy=False, stored='normal'
        )
        == 'normal'
    )


def test_paranoia_flags_match_cdparanoia_semantics() -> None:
    """Archival secure: -X abort-on-skip. Normal: repair ok. Burst: -Z."""
    secure = secure_paranoia_flags()
    normal = normal_paranoia_flags()
    burst = burst_paranoia_flags()

    assert '-X' in secure
    assert any(f.startswith('--never-skip=') for f in secure)
    assert '-o' in secure
    assert '-e' in secure

    assert '-X' not in normal
    assert any(f.startswith('--never-skip=') for f in normal)
    assert '-Z' not in normal

    assert '-Z' in burst
    assert '-X' not in burst

    assert paranoia_flags_for_mode('secure') == secure
    assert paranoia_flags_for_mode('archival') == secure  # unknown → secure
    assert paranoia_flags_for_mode('normal') == normal
    assert paranoia_flags_for_mode('burst') == burst


def test_paranoia_cmd_base_uses_mode_flags() -> None:
    eng = RipEngine()
    secure = eng._paranoia_cmd_base('cdparanoia', '/dev/sr0', mode='secure')
    normal = eng._paranoia_cmd_base('cdparanoia', '/dev/sr0', mode='normal')
    burst = eng._paranoia_cmd_base('cdparanoia', '/dev/sr0', mode='burst')
    assert '-X' in secure
    assert '-X' not in normal
    assert '-Z' in burst
    assert '-Z' not in secure
    assert '-Z' not in normal


def test_derive_extract_flags() -> None:
    assert derive_extract_flags('archival') == ('archival', False, True)
    assert derive_extract_flags('normal') == ('normal', False, False)
    assert derive_extract_flags('burst') == ('burst', True, False)


def _job(**kwargs: object) -> RipJob:
    from pathlib import Path

    base: dict[str, object] = {
        'device': '/dev/sr0',
        'track_numbers': [1],
        'output_directory': Path('/tmp/music'),
    }
    base.update(kwargs)
    return RipJob(**base)  # type: ignore[arg-type]


def test_job_extract_mode_tokens() -> None:
    archival = _job(rip_mode='archival')
    sync_job_extract_flags(archival)
    assert archival.test_and_copy is True
    assert archival.burst_mode is False
    assert job_extract_mode(archival) == 'secure'

    normal = _job(rip_mode='normal')
    sync_job_extract_flags(normal)
    assert normal.test_and_copy is False
    assert normal.burst_mode is False
    assert job_extract_mode(normal) == 'normal'

    burst = _job(rip_mode='burst')
    sync_job_extract_flags(burst)
    assert burst.test_and_copy is False
    assert burst.burst_mode is True
    assert job_extract_mode(burst) == 'burst'


def test_format_extract_mode_labels() -> None:
    from pathlib import Path

    from ready2rip.rip.engine_extract import ExtractMixin
    from ready2rip.rip.engine_types import _TrackExtract
    from ready2rip.rip.paranoia_stats import ParanoiaStats

    def _ext(mode: str) -> _TrackExtract:
        return _TrackExtract(
            mode=mode,
            test_crc='',
            copy_crc='',
            peak=None,
            elapsed=0.0,
            stats=ParanoiaStats(),
            matched=True,
            copy_wav=Path('/tmp/c'),
            test_wav=Path('/tmp/t'),
            dual_pass=False,
        )

    assert 'test & copy' in ExtractMixin._format_extract_mode(
        _TrackExtract(
            mode='secure',
            test_crc='a',
            copy_crc='a',
            peak=None,
            elapsed=1.0,
            stats=ParanoiaStats(),
            matched=True,
            copy_wav=Path('/tmp/c'),
            test_wav=Path('/tmp/t'),
            dual_pass=True,
            test_mode='secure',
            copy_mode='secure',
        ),
        test_and_copy=True,
    )
    assert 'normal' in ExtractMixin._format_extract_mode(
        _ext('normal'), test_and_copy=False
    )
    assert 'burst' in ExtractMixin._format_extract_mode(
        _ext('burst'), test_and_copy=False
    )


def test_apply_extract_to_log_no_self_nameerror() -> None:
    """Regression: staticmethod must not reference ``self`` when filling the log."""
    from pathlib import Path
    from types import SimpleNamespace

    from ready2rip.rip.engine_extract import ExtractMixin
    from ready2rip.rip.engine_types import _TrackExtract
    from ready2rip.rip.paranoia_stats import ParanoiaStats

    entry = SimpleNamespace(
        extract_mode='',
        extract_seconds=None,
        extract_speed_x=None,
        test_crc='',
        copy_crc='',
        peak=None,
        quality=None,
        notes=[],
        expected_wav_bytes=0,
        wav_bytes=0,
    )
    extracted = _TrackExtract(
        mode='normal',
        test_crc='',
        copy_crc='abcd',
        peak=0.5,
        elapsed=2.0,
        stats=ParanoiaStats(),
        matched=True,
        copy_wav=Path('/tmp/c.wav'),
        test_wav=Path('/tmp/t.wav'),
        dual_pass=False,
        expected_wav_bytes=1000,
    )
    ExtractMixin._apply_extract_to_log(
        entry,
        extracted=extracted,
        sectors=75,
        expected_bytes=1000,
        test_and_copy=False,
    )
    assert 'normal' in entry.extract_mode


def test_apply_extract_to_log_intentional_burst_not_errors() -> None:
    from pathlib import Path
    from types import SimpleNamespace

    from ready2rip.rip.engine_extract import ExtractMixin
    from ready2rip.rip.engine_types import _TrackExtract
    from ready2rip.rip.paranoia_stats import ParanoiaStats

    entry = SimpleNamespace(
        extract_mode='',
        extract_seconds=None,
        extract_speed_x=None,
        test_crc='',
        copy_crc='',
        peak_percent=None,
        quality_percent=None,
        error_correction_lines=[],
        notes=[],
        expected_wav_bytes=0,
        wav_bytes=0,
        had_errors=False,
        status='',
        attempt_log=[],
        crc_verified=None,
    )
    extracted = _TrackExtract(
        mode='burst',
        test_crc='',
        copy_crc='abcd',
        peak=0.5,
        elapsed=2.0,
        stats=ParanoiaStats(),
        matched=True,
        copy_wav=Path('/tmp/c.wav'),
        test_wav=Path('/tmp/t.wav'),
        dual_pass=False,
        expected_wav_bytes=1000,
    )
    ExtractMixin._apply_extract_to_log(
        entry,
        extracted=extracted,
        sectors=75,
        expected_bytes=1000,
        test_and_copy=False,
        intentional_burst=True,
    )
    assert entry.had_errors is False
    assert entry.status == 'OK'
    assert any('paranoia disabled' in n for n in entry.notes)
    assert not any('Secure paranoia failed' in n for n in entry.notes)

    entry.notes.clear()
    ExtractMixin._apply_extract_to_log(
        entry,
        extracted=extracted,
        sectors=75,
        expected_bytes=1000,
        test_and_copy=False,
        intentional_burst=False,
    )
    assert entry.had_errors is True
    assert entry.status == 'finished with errors'
    assert any('Secure paranoia failed' in n for n in entry.notes)

