# SPDX-License-Identifier: GPL-3.0-or-later
"""ReplayGain → iTunes Sound Check (iTunNORM) conversion (gi-free)."""

from __future__ import annotations

# Classic iPod / iTunes Sound Check containers (not FLAC/Opus).
IPOD_SOUNDCHECK_SUFFIXES = frozenset({'.mp3', '.m4a', '.mp4', '.aac', '.aiff', '.aif', '.wav'})


def supports_sound_check(path_suffix: str) -> bool:
    """True when *path_suffix* is an iPod-compatible format for iTunNORM."""
    return path_suffix.lower() in IPOD_SOUNDCHECK_SUFFIXES


def replaygain_to_itunnorm(gain_db: float, peak: float) -> str:
    """Build the 10-field ``iTunNORM`` string from ReplayGain gain/peak.

    Uses the widely documented rg2sc mapping (milliWatt / 1/2500 Watt pairs +
    16-bit peak). Album or track gain may be passed; callers choose.
    """
    gain = float(gain_db)
    peak_f = max(0.0, float(peak))
    # Absolute RMS-ish units; clamp like other converters (~ -18.2 dB floor).
    g1 = min(round((10.0 ** (gain / -10.0)) * 1000.0), 65534)
    g2 = min(round((10.0 ** (gain / -10.0)) * 2500.0), 65534)
    peak_sample = min(round(peak_f * 32768.0), 65534)
    uk = 0
    values = (g1, g1, g2, g2, uk, uk, peak_sample, peak_sample, uk, uk)
    return (' %08X' * 10) % values
