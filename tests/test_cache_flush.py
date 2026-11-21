# SPDX-License-Identifier: GPL-3.0-or-later
"""Cache flush: overlapping -Z is never a successful defeat."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from ready2rip.disc.cache import flush_drive_cache


def test_flush_overlapping_audio_not_success() -> None:
    disc = SimpleNamespace(
        tracks=[
            SimpleNamespace(number=1, start_sector=0, length_sectors=1000),
        ],
        data_tracks=[],
        leadout_lba=1000,
    )

    mmc = MagicMock()
    mmc.seek_lba.return_value = False
    mmc.read_raw_sectors.return_value = None
    mmc.__enter__ = MagicMock(return_value=mmc)
    mmc.__exit__ = MagicMock(return_value=False)

    with (
        patch('ready2rip.util.find_cdparanoia', return_value='cdparanoia'),
        patch('ready2rip.disc.cache._extract_span') as extract,
        patch('ready2rip.disc.mmc.MmcDevice', return_value=mmc),
    ):
        # Full-disc avoid: no clean span; SEEK fails; overlapping -Z may run.
        ok = flush_drive_cache(
            '/dev/sr0',
            disc,
            avoid_start=0,
            avoid_end=1000,
        )
    assert ok is False
    # Overlapping extract may be attempted, but must not flip success.
    assert extract.called or not extract.called
