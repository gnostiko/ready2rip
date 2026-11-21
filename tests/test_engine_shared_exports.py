# SPDX-License-Identifier: GPL-3.0-or-later
"""Mixin star-imports must receive underscore helpers from ``_engine_shared``."""

from __future__ import annotations

from ready2rip.rip.engine_extract import ExtractMixin

# Names that previously vanished under ``from … import *`` (leading ``_``).
_REQUIRED = (
    '_TrackExtract',
    '_PendingEncode',
    '_CrcMismatch',
    '_extract_timeout',
    '_expected_wav_sizes',
    '_prepare_artwork',
    '_retag_title',
    '_track_avoid_span',
    '_job_per_track_overread_span',
    'paranoia_flags_for_mode',
    'secure_paranoia_flags',
    'normal_paranoia_flags',
    'burst_paranoia_flags',
)


def test_extract_mixin_globals_include_private_shared_names() -> None:
    ns = ExtractMixin._extract_track_burst.__globals__
    missing = [name for name in _REQUIRED if name not in ns]
    assert missing == []


def test_paranoia_mixin_has_paranoia_flags_for_mode() -> None:
    from ready2rip.rip.engine_paranoia import ParanoiaMixin

    ns = ParanoiaMixin._paranoia_cmd_base.__globals__
    assert 'paranoia_flags_for_mode' in ns
