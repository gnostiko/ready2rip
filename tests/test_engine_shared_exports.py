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
)


def test_extract_mixin_globals_include_private_shared_names() -> None:
    ns = ExtractMixin._extract_track_burst.__globals__
    missing = [name for name in _REQUIRED if name not in ns]
    assert missing == []
