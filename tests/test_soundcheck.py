# SPDX-License-Identifier: GPL-3.0-or-later
"""iTunes Sound Check (iTunNORM) conversion helpers (no PyGObject)."""

from __future__ import annotations

from ready2rip.rip.soundcheck import (
    replaygain_to_itunnorm,
    supports_sound_check,
)


def test_ipod_compatible_suffixes() -> None:
    assert supports_sound_check('.mp3')
    assert supports_sound_check('.M4A')
    assert supports_sound_check('.aiff')
    assert supports_sound_check('.wav')
    assert not supports_sound_check('.flac')
    assert not supports_sound_check('.opus')


def test_rg_to_itunnorm_shape_and_unity_ish() -> None:
    # 0 dB gain, full-scale peak → reference 1000 / 2500 and peak 32768.
    text = replaygain_to_itunnorm(0.0, 1.0)
    parts = text.strip().split()
    assert len(parts) == 10
    assert all(len(p) == 8 for p in parts)
    assert int(parts[0], 16) == 1000
    assert int(parts[2], 16) == 2500
    assert int(parts[6], 16) == 32768


def test_rg_to_itunnorm_louder_track_raises_units() -> None:
    quiet = replaygain_to_itunnorm(+6.0, 0.5)
    loud = replaygain_to_itunnorm(-6.0, 0.5)
    q0 = int(quiet.strip().split()[0], 16)
    l0 = int(loud.strip().split()[0], 16)
    # Negative gain (loud track) → larger Sound Check attenuation units.
    assert l0 > q0


def test_track_gains_produce_distinct_itunnorm() -> None:
    a = replaygain_to_itunnorm(-3.0, 0.8)
    b = replaygain_to_itunnorm(+2.0, 0.4)
    assert a != b
