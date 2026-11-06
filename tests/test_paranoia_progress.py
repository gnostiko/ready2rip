# SPDX-License-Identifier: GPL-3.0-or-later
"""Live extract-progress streaming (cdparanoia -e / WAV growth).

Kept free of PyGObject so CI unit jobs without ``gi`` still collect it.
"""

from __future__ import annotations

import stat
import sys
import time
from pathlib import Path

from ready2rip.rip.paranoia_run import _WRITE_PROGRESS_SPAN, run_paranoia_streaming
from ready2rip.rip.paranoia_stats import progress_frame_from_line


def test_progress_frame_from_line() -> None:
    assert progress_frame_from_line('##: 0 [read] @ 19992') == 19992
    assert progress_frame_from_line('##: 15 [finished] @ 88200') == 88200
    assert progress_frame_from_line('not a progress line') is None


def test_run_paranoia_emits_monotonic_progress(tmp_path: Path) -> None:
    """Fake extractor that prints -e lines and grows a WAV must drive callbacks."""
    script = tmp_path / 'fake_paranoia.py'
    wav = tmp_path / 'track.wav'
    script.write_text(
        '''\
import sys
import time
from pathlib import Path

out = Path(sys.argv[1])
out.write_bytes(b"RIFFxxxxWAVEfmt " + bytes(28))  # 44-byte stub
frames = [0, 2500, 5000, 10000, 15000, 20000]
for frame in frames:
    print(f"##: 0 [read] @ {frame}", flush=True, file=sys.stderr)
    with out.open("ab") as fh:
        fh.write(b"\\x00" * 2352 * 4)
    time.sleep(0.04)
print("##: 15 [finished] @ 20000", flush=True, file=sys.stderr)
''',
        encoding='utf-8',
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)

    seen: list[float] = []
    expected_bytes = 44 + 2352 * 4 * 6
    stats = run_paranoia_streaming(
        [sys.executable, str(script), str(wav)],
        what='fake paranoia',
        timeout=15,
        on_extract_progress=seen.append,
        expected_frames=20_000,
        wav_path=wav,
        expected_bytes=expected_bytes,
    )
    assert stats.finished or stats.reads >= 1
    assert seen, 'expected live progress callbacks during extract'
    assert seen[-1] == 1.0
    assert seen == sorted(seen)
    # Write-span mapping: mid-extract values stay below the verify headroom.
    mid = [v for v in seen if 0.05 <= v < 1.0]
    assert mid, 'expected intermediate progress'
    assert max(mid) <= 0.99 + 1e-9
    assert any(v <= _WRITE_PROGRESS_SPAN + 1e-9 for v in mid)


def test_run_paranoia_creeps_during_reread_stall(tmp_path: Path) -> None:
    """After the WAV is full, re-read -e lines must still advance progress."""
    script = tmp_path / 'fake_paranoia_stall.py'
    wav = tmp_path / 'track.wav'
    script.write_text(
        '''\
import sys
import time
from pathlib import Path

out = Path(sys.argv[1])
# Pretend the write pass finished immediately (full TOC-sized WAV).
header = b"RIFFxxxxWAVEfmt " + bytes(28)
pcm = b"\\x00" * (2352 * 40)
out.write_bytes(header + pcm)
# Forward read to the end, then re-verify earlier frames for a few seconds.
print("##: 0 [read] @ 0", flush=True, file=sys.stderr)
time.sleep(0.05)
print("##: 0 [read] @ 23520", flush=True, file=sys.stderr)
time.sleep(0.05)
for frame in (20000, 15000, 10000, 5000, 0, 8000, 12000, 18000, 22000):
    print(f"##: 2 [verify] @ {frame}", flush=True, file=sys.stderr)
    time.sleep(0.35)
print("##: 15 [finished] @ 23520", flush=True, file=sys.stderr)
''',
        encoding='utf-8',
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)

    seen: list[float] = []
    expected_bytes = 44 + 2352 * 40
    t0 = time.monotonic()
    stats = run_paranoia_streaming(
        [sys.executable, str(script), str(wav)],
        what='fake paranoia stall',
        timeout=20,
        on_extract_progress=seen.append,
        expected_frames=23_520,
        wav_path=wav,
        expected_bytes=expected_bytes,
    )
    elapsed = time.monotonic() - t0
    assert elapsed >= 2.0  # re-verify phase actually ran
    assert stats.finished or stats.verifies >= 1 or stats.reads >= 1
    assert seen, 'expected progress callbacks'
    assert seen == sorted(seen)
    assert seen[-1] == 1.0
    # Must keep moving after the write span while re-reads are happening.
    after_write = [v for v in seen if v > _WRITE_PROGRESS_SPAN]
    assert after_write, (
        f'expected creep past write span {_WRITE_PROGRESS_SPAN}; saw {seen!r}'
    )
    assert max(after_write) >= _WRITE_PROGRESS_SPAN + 0.01


def test_per_track_step_fraction() -> None:
    """Banner fill advances only when a track rip completes (sub=1.0)."""
    n_tracks = 12
    fractions: list[float] = []
    last = 0.0
    for i in range(n_tracks):
        # Mid-rip status updates must not move the fill.
        mid = max(last, i / n_tracks)
        last = mid
        fractions.append(mid)
        # Track finished → jump to (i+1)/n.
        done = max(last, (i + 1) / n_tracks)
        last = done
        fractions.append(done)
    assert fractions[0] == 0.0
    assert abs(fractions[-1] - 1.0) < 1e-9
    # Exactly one distinct step per completed track.
    steps = sorted({round(f, 6) for f in fractions})
    assert len(steps) == n_tracks + 1
    assert steps == [round(i / n_tracks, 6) for i in range(n_tracks + 1)]
