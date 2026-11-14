# SPDX-License-Identifier: GPL-3.0-or-later
"""Overread / offset silence-risk helpers."""

from __future__ import annotations

from ready2rip.disc.overread import OverreadCapability, offset_silence_risk


def test_offset_silence_risk_zero_offset_ok() -> None:
    assert (
        offset_silence_risk(
            0,
            OverreadCapability(),
            includes_disc_start=True,
            includes_disc_end=True,
        )
        is None
    )


def test_offset_silence_risk_needs_lead_in() -> None:
    msg = offset_silence_risk(
        -6,
        OverreadCapability(lead_in=False, lead_out=True),
        includes_disc_start=True,
        includes_disc_end=False,
    )
    assert msg is not None
    assert 'lead-in' in msg


def test_offset_silence_risk_both_ok() -> None:
    assert (
        offset_silence_risk(
            667,
            OverreadCapability(lead_in=True, lead_out=True),
            includes_disc_start=True,
            includes_disc_end=True,
        )
        is None
    )


def test_offset_silence_risk_middle_tracks_ok_without_overread() -> None:
    assert (
        offset_silence_risk(
            48,
            OverreadCapability(),
            includes_disc_start=False,
            includes_disc_end=False,
        )
        is None
    )
