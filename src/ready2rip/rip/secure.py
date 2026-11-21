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

# Reject WAVs short of the TOC-expected CDDA size. Archival secure
# (min_ratio ≥ 1.0) requires exact TOC length — no sector of slack.
# Non-secure / burst paths may pass a lower min_ratio for recovery.
SECURE_MIN_WAV_RATIO = 1.0
# Legacy alias kept for callers/tests that still name the old one-sector slack.
SECURE_WAV_SHORT_BYTES = 2352

# Extra full test+copy cycles after a CRC mismatch (total attempts = 1 + N).
SECURE_TEST_COPY_RETRIES = 3


def wav_size_minimum(expected_bytes: int, min_ratio: float) -> int:
    """Smallest acceptable WAV size for a TOC-expected *expected_bytes*.

    When *min_ratio* ≥ 1.0 (archival secure), require the full expected size.
    Lower ratios (burst / recovery) scale the floor without the old one-sector
    slack used for secure dual-pass.
    """
    expected = max(0, int(expected_bytes))
    if expected <= 0:
        return 0
    if float(min_ratio) >= 1.0:
        return expected
    return int(expected * max(0.0, float(min_ratio)))


def secure_paranoia_flags() -> list[str]:
    """cdparanoia argv flags for archival secure pass (no span/track)."""
    return [
        # One token — "-z" "500" is misparsed as track 500.
        f'--never-skip={SECURE_NEVER_SKIP}',
        '-X',  # abort rather than silently keep a skip
        # Two tokens — cdparanoia III takes -o <n>, not --force-search-overlap=n.
        '-o',
        str(SECURE_MIN_OVERLAP),
        '-e',  # progress / error-correction callbacks on stderr
    ]


def normal_paranoia_flags() -> list[str]:
    """Paranoia with repair/interpolation allowed (no abort-on-skip)."""
    return [
        f'--never-skip={SECURE_NEVER_SKIP}',
        '-o',
        str(SECURE_MIN_OVERLAP),
        '-e',
    ]


def burst_paranoia_flags() -> list[str]:
    """Insecure -Z pass (optional recovery only)."""
    return ['-Z', '-e']


def paranoia_flags_for_mode(mode: str) -> list[str]:
    """Flags for extract *mode*: ``secure``, ``normal``, or ``burst``."""
    key = (mode or '').strip().lower()
    if key == 'burst':
        return burst_paranoia_flags()
    if key == 'normal':
        return normal_paranoia_flags()
    return secure_paranoia_flags()
