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


def test_decode_existing_rejects_unknown_m4a(tmp_path: Path) -> None:
    src = tmp_path / 'a.m4a'
    src.write_bytes(b'ftypisomfake')
    dest = tmp_path / 'b.wav'
    assert not RipEngine._decode_existing_to_wav(src, dest)


def test_empty_image_pcm_slice_appends_errors() -> None:
    """Empty TOC slices must not skip AR/CTDB result lists (Bugbot high)."""
    from ready2rip.accuraterip import AccurateRipConfidence, AccurateRipResult
    from ready2rip.ctdb import CtdbConfidence, CtdbResult
    from ready2rip.disc.probe import DiscInfo, TrackInfo
    from ready2rip.rip.riplog import RipLog

    # Simulate the empty-slice branch of _verify_image_databases.
    disc = DiscInfo(
        device='/dev/sr0',
        tracks=[TrackInfo(number=1, start_sector=0, length_sectors=100)],
    )
    ar_results: list[AccurateRipResult] = []
    ctdb_results: list[CtdbResult] = []
    rip_log = RipLog()
    track = disc.tracks[0]
    msg = f'Track {track.number}: empty PCM slice in image'
    entry = rip_log.begin_track(track.number)
    entry.status = 'ERROR'
    entry.had_errors = True
    ar = AccurateRipResult(track.number, AccurateRipConfidence.ERROR, message=msg)
    ctdb = CtdbResult(track.number, CtdbConfidence.ERROR, message=msg)
    ar_results.append(ar)
    ctdb_results.append(ctdb)
    entry.accuraterip = ar
    entry.ctdb = ctdb
    from pathlib import Path

    from ready2rip.rip.engine import RipJob
    from ready2rip.rip.outcome import archival_fail_reasons

    reasons = archival_fail_reasons(
        RipJob(
            device='/dev/sr0',
            track_numbers=[1],
            output_directory=Path('/tmp'),
            verify_accuraterip=True,
            verify_ctdb=True,
            copy_image=True,
        ),
        ar_results=ar_results,
        ctdb_results=ctdb_results,
        burst_tracks=[],
    )
    assert any('AccurateRip error' in r for r in reasons)
    assert any('CTDB error' in r for r in reasons)


def test_record_image_verify_load_failure_appends_errors() -> None:
    from ready2rip.accuraterip import AccurateRipConfidence
    from ready2rip.ctdb import CtdbConfidence
    from ready2rip.disc.probe import DiscInfo, TrackInfo
    from ready2rip.rip.riplog import RipLog

    disc = DiscInfo(
        device='/dev/sr0',
        tracks=[
            TrackInfo(number=1, start_sector=0, length_sectors=1000),
            TrackInfo(number=2, start_sector=1000, length_sectors=1000),
        ],
    )
    ar_results: list = []
    ctdb_results: list = []
    rip_log = RipLog()
    RipEngine._record_image_verify_load_failure(
        disc,
        message='Image PCM load failed: boom',
        extract_mode='secure',
        ar_verifier=object(),  # truthy sentinel
        ctdb_verifier=object(),
        ar_results=ar_results,
        ctdb_results=ctdb_results,
        rip_log=rip_log,
    )
    assert len(ar_results) == 2
    assert len(ctdb_results) == 2
    assert all(r.confidence == AccurateRipConfidence.ERROR for r in ar_results)
    assert all(r.confidence == CtdbConfidence.ERROR for r in ctdb_results)
    assert len(rip_log.tracks) == 2
