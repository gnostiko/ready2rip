# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for CUETools Database TOC / CRC / XML parsing."""

from __future__ import annotations

import wave
import zlib
from pathlib import Path

import pytest

from ready2rip.ctdb import (
    CTDB_STRIDE,
    CtdbConfidence,
    CtdbDatabase,
    CtdbEntry,
    compute_track_crc,
    laststride_samples,
    parse_lookup_xml,
    toc_string_from_info,
    total_audio_samples,
    track_crc_bounds,
)
from ready2rip.disc.probe import DataTrackInfo, DiscInfo, TrackInfo


def _disc(*starts_and_lengths: tuple[int, int], data=()) -> DiscInfo:
    tracks = [
        TrackInfo(number=i, start_sector=start, length_sectors=length)
        for i, (start, length) in enumerate(starts_and_lengths, start=1)
    ]
    data_tracks = [
        DataTrackInfo(number=n, start_sector=s, length_sectors=length) for n, s, length in data
    ]
    return DiscInfo(device="/dev/sr0", tracks=tracks, data_tracks=data_tracks)


def test_toc_string_relative_audio_only() -> None:
    disc = _disc((0, 14741), (14741, 11266), (26007, 13232), (39239, 14898))
    assert toc_string_from_info(disc) == "0:14741:26007:39239:54137"


def test_toc_string_with_cd_extra_data() -> None:
    disc = _disc(
        (0, 14017),
        (14017, 12822),
        data=((3, 186137, 38754),),
    )
    # Lead-out follows the data track end.
    assert toc_string_from_info(disc) == "0:14017:-186137:224891"


def test_total_audio_samples_and_laststride() -> None:
    disc = _disc((0, 100), (100, 200))
    samples = total_audio_samples(disc)
    assert samples == 300 * 588
    assert laststride_samples(samples) == CTDB_STRIDE + (samples * 2) % CTDB_STRIDE
    assert laststride_samples(58800) == CTDB_STRIDE


def test_track_crc_bounds_first_middle_last() -> None:
    total_samples = 588 * 400
    suffix = laststride_samples(total_samples) // 2
    prefix = CTDB_STRIDE // 2

    # Middle track: full PCM.
    mid_len = 1000 * 4
    assert track_crc_bounds(mid_len, 2, 3, total_samples) == (0, mid_len)

    # First track: skip prefix samples.
    first_len = (prefix + 500) * 4
    assert track_crc_bounds(first_len, 1, 3, total_samples) == (prefix * 4, first_len)

    # Last track: drop suffix samples.
    last_len = (suffix + 500) * 4
    assert track_crc_bounds(last_len, 3, 3, total_samples) == (0, last_len - suffix * 4)


def test_compute_track_crc_matches_zlib_slice() -> None:
    total_samples = 588 * 200
    prefix = CTDB_STRIDE // 2
    pcm = bytes((i * 17) % 256 for i in range((prefix + 800) * 4))
    expected = zlib.crc32(pcm[prefix * 4 :]) & 0xFFFFFFFF
    assert compute_track_crc(pcm, 1, 2, total_samples) == expected


def test_parse_lookup_xml_and_best_match() -> None:
    xml = """<?xml version="1.0"?>
<ctdb xmlns="http://db.cuetools.net/ns/mmd-1.0#">
 <entry confidence="11" crc32="843c89cb" id="10631343"
        toc="0:14741:26007:39239:54137"
        parity="cGFyaXR5" syndrome="c3luZA==" npar="8"
        hasparity="http://p.cuetools.net/10631343"
        trackcrcs="797874e7 b8492504 4b87b5de d29262a2" />
 <entry confidence="3" crc32="aaaaaaaa" id="2"
        trackcrcs="797874e7 00000000 4b87b5de d29262a2" />
 <metadata />
</ctdb>
"""
    db = parse_lookup_xml(xml, toc="0:14741:26007:39239:54137")
    assert len(db.entries) == 2
    assert db.entries[0].parity == "cGFyaXR5"
    assert db.entries[0].syndrome == "c3luZA=="
    assert db.entries[0].npar == 8
    assert db.entries[0].hasparity == "http://p.cuetools.net/10631343"
    assert db.max_entry_id == 10631343
    match = db.best_match(1, 0x797874E7)
    assert match.confidence == CtdbConfidence.MATCH
    assert match.confidence_count == 14  # 11 + 3
    miss = db.best_match(2, 0xB8492504)
    assert miss.confidence == CtdbConfidence.MATCH
    assert miss.confidence_count == 11
    none = db.best_match(2, 0xDEADBEEF)
    assert none.confidence == CtdbConfidence.MISMATCH
    crcs = [0x797874E7, 0xB8492504, 0x4B87B5DE, 0xD29262A2]
    entry = db.entry_matching_track_crcs(crcs)
    assert entry is not None
    assert entry.entry_id == "10631343"
    assert db.entry_matching_track_crcs([1, 2, 3, 4]) is None


def test_parse_submit_response_success() -> None:
    from ready2rip.ctdb import _parse_submit_response

    raw = b"""<?xml version="1.0"?>
<ctdb xmlns="http://db.cuetools.net/ns/mmd-1.0#">
 <response status="success" message="entry confirmed" />
</ctdb>
"""
    result = _parse_submit_response(raw)
    assert result.ok
    assert result.status == "success"


def test_crc32_to_signed() -> None:
    from ready2rip.ctdb import _crc32_to_signed

    assert _crc32_to_signed("843c89cb") < 0
    assert _crc32_to_signed(0x00000001) == 1
    assert _crc32_to_signed(0xFFFFFFFF) == -1


def test_empty_database_not_in_db() -> None:
    db = CtdbDatabase(toc="0:1:2")
    result = db.best_match(1, 0)
    assert result.confidence == CtdbConfidence.NOT_IN_DB


def test_compute_track_crc_wav(tmp_path: Path) -> None:
    samples = CTDB_STRIDE // 2 + 600
    frames = b"\x01\x00\x02\x00" * samples
    path = tmp_path / "t1.wav"
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(44100)
        wf.writeframes(frames)
    from ready2rip.ctdb import compute_track_crc_wav

    crc = compute_track_crc_wav(path, 1, 2, 588 * 100)
    assert isinstance(crc, int)
    assert 0 <= crc <= 0xFFFFFFFF


@pytest.mark.network
def test_live_ctdb_lookup_known_toc() -> None:
    from ready2rip.ctdb import fetch_database

    db = fetch_database("0:14741:26007:39239:54137", timeout=20.0)
    assert db is not None
    assert isinstance(db.entries[0] if db.entries else CtdbEntry("", 0, "", []), CtdbEntry)
    # Disc is known to be in CTDB; tolerate empty if the service is down.
    if db.entries:
        assert db.entries[0].track_crcs
        assert db.entries[0].confidence > 0
