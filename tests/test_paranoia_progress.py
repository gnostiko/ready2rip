# SPDX-License-Identifier: GPL-3.0-or-later
"""Live extract-progress streaming (cdparanoia -e / WAV growth).

Kept free of PyGObject so CI unit jobs without ``gi`` still collect it.
"""

from __future__ import annotations

import stat
import sys
from pathlib import Path

from ready2rip.rip.paranoia_run import run_paranoia_streaming
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
    assert any(0.05 <= v < 1.0 for v in seen)
