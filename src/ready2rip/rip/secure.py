# SPDX-License-Identifier: GPL-3.0-or-later
"""Secure extraction defaults for cdparanoia / libcdio-paranoia.

Archival secure mode: dual-pass CRC verification, cache defeat, drive offset
correction, and no silent acceptance of imperfect reads. Full paranoia,
never-skip persistence, abort-on-skip, forced overlap search, and test & copy.
"""

from __future__ import annotations

# Re-read imperfect data this many times before allowing a skip attempt.
SECURE_NEVER_SKIP = 500

# Minimum sectors of overlap search during verification (-o).
# Forces more thorough jitter/overlap checking than drive defaults.
SECURE_MIN_OVERLAP = 8

# Reject WAVs short of the TOC-expected CDDA size. Prefer exact length;
# allow at most one CDDA sector of slack for edge rounding.
SECURE_MIN_WAV_RATIO = 1.0
SECURE_WAV_SHORT_BYTES = 2352

# Extra full test+copy cycles after a CRC mismatch (total attempts = 1 + N).
SECURE_TEST_COPY_RETRIES = 3


def wav_size_minimum(expected_bytes: int, min_ratio: float) -> int:
    """Smallest acceptable WAV size for a TOC-expected *expected_bytes*."""
    expected = max(0, int(expected_bytes))
    if expected <= 0:
        return 0
    if float(min_ratio) >= 1.0:
        return max(0, expected - SECURE_WAV_SHORT_BYTES)
    return int(expected * max(0.0, float(min_ratio)))


def secure_paranoia_flags() -> list[str]:
    """cdparanoia argv flags for a secure pass (no span/track)."""
    return [
        # One token — "-z" "500" is misparsed as track 500.
        f'--never-skip={SECURE_NEVER_SKIP}',
        '-X',  # abort rather than silently keep a skip
        # Two tokens — cdparanoia III takes -o <n>, not --force-search-overlap=n.
        '-o',
        str(SECURE_MIN_OVERLAP),
        '-e',  # progress / error-correction callbacks on stderr
    ]


def burst_paranoia_flags() -> list[str]:
    """Insecure -Z pass (optional recovery only)."""
    return ['-Z', '-e']
