# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for AccurateRip disc IDs and checksum helpers."""

from __future__ import annotations

from ready2rip.accuraterip import (
    AccurateRipConfidence,
    AccurateRipDatabase,
    compute_checksums_samples,
    disc_ids_from_info,
)
from ready2rip.disc.probe import DiscInfo, TrackInfo


def test_disc_ids_from_info_stable() -> None:
    disc = DiscInfo(
        device='/dev/sr0',
        tracks=[
            TrackInfo(number=1, start_sector=0, length_sectors=15000),
            TrackInfo(number=2, start_sector=15000, length_sectors=20000),
        ],
        freedb_id='a10b0c0d',
    )
    ids = disc_ids_from_info(disc)
    assert ids is not None
    assert ids.num_tracks == 2
    assert ids.freedb_id == 'a10b0c0d'
    assert len(ids.ar_id1) == 8
    assert len(ids.ar_id2) == 8
    assert ids.disc_id_string.startswith('002-')


def test_compute_checksums_samples_deterministic() -> None:
    # 12 sectors of silence is enough for first/last skip windows.
    n = 12 * 588
    silence = memoryview(b'\x00\x00\x00\x00' * n).cast('I')
    v1, v2 = compute_checksums_samples(silence, 1, 1, sample_offset=0)
    assert v1 == 0
    assert v2 == 0


def test_accurate_rip_database_best_match() -> None:
    db = AccurateRipDatabase()
    db.by_track[1] = [(0x11111111, 4), (0x22222222, 9)]
    match = db.best_match(1, 0x22222222, 0)
    assert match.confidence == AccurateRipConfidence.MATCH
    assert match.matched_version == 'v1'
    assert match.confidence_count == 9
    miss = db.best_match(1, 0xDEADBEEF, 0xCAFEBABE)
    assert miss.confidence == AccurateRipConfidence.MISMATCH
    missing = db.best_match(2, 1, 2)
    assert missing.confidence == AccurateRipConfidence.NOT_IN_DB
