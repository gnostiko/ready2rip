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

# Reject WAVs that are meaningfully short of the TOC-expected CDDA size.
SECURE_MIN_WAV_RATIO = 0.995

# Extra full test+copy cycles after a CRC mismatch (total attempts = 1 + N).
SECURE_TEST_COPY_RETRIES = 3


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
