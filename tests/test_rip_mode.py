# SPDX-License-Identifier: GPL-3.0-or-later
"""Rip mode migration and paranoia flag selection."""

from __future__ import annotations

from ready2rip.rip.secure import (
    normal_paranoia_flags,
    paranoia_flags_for_mode,
    secure_paranoia_flags,
)
from ready2rip.settings_defs import (
    migrate_rip_mode_from_legacy,
    normalize_rip_mode,
)


def test_normalize_rip_mode() -> None:
    assert normalize_rip_mode('ARCHIVAL') == 'archival'
    assert normalize_rip_mode('nope') == 'archival'


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


def test_normal_paranoia_allows_repair() -> None:
    flags = normal_paranoia_flags()
    assert '-X' not in flags
    assert any(f.startswith('--never-skip=') for f in flags)
    assert '-X' in secure_paranoia_flags()
    assert paranoia_flags_for_mode('normal') == flags
    assert '-Z' in paranoia_flags_for_mode('burst')


def test_derive_extract_flags() -> None:
    from ready2rip.settings_defs import derive_extract_flags

    assert derive_extract_flags('archival') == ('archival', False, True)
    assert derive_extract_flags('normal') == ('normal', False, False)
    assert derive_extract_flags('burst') == ('burst', True, False)
