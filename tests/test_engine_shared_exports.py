# SPDX-License-Identifier: GPL-3.0-or-later
"""Mixin star-imports must receive helpers from ``_engine_shared``."""

from __future__ import annotations

import builtins
import dis
import types

from ready2rip.rip.engine_copy_image import CopyImageMixin
from ready2rip.rip.engine_encode import EncodeMixin
from ready2rip.rip.engine_extract import ExtractMixin
from ready2rip.rip.engine_paranoia import ParanoiaMixin
from ready2rip.rip.engine_verify import VerifyMixin

# Names that previously vanished under ``from … import *`` (leading ``_``)
# or were never wired into the shared hub.
_REQUIRED = (
    '_TrackExtract',
    '_PendingEncode',
    '_CrcMismatch',
    '_extract_timeout',
    '_expected_wav_sizes',
    '_prepare_artwork',
    '_retag_title',
    '_track_avoid_span',
    'per_track_overread_read_span',
    'paranoia_flags_for_mode',
    'secure_paranoia_flags',
    'normal_paranoia_flags',
    'burst_paranoia_flags',
    'rip_scratch_dir',
    'evaluate_archival',
    'as_completed',
    'open_cdda_wav_mmap',
    'image_track_pcm_from_view',
)

_MIXINS = (
    ExtractMixin,
    ParanoiaMixin,
    CopyImageMixin,
    EncodeMixin,
    VerifyMixin,
)


def test_extract_mixin_globals_include_private_shared_names() -> None:
    ns = ExtractMixin._extract_track_burst.__globals__
    missing = [name for name in _REQUIRED if name not in ns]
    assert missing == []


def test_paranoia_mixin_has_paranoia_flags_for_mode() -> None:
    ns = ParanoiaMixin._paranoia_cmd_base.__globals__
    assert 'paranoia_flags_for_mode' in ns


def _missing_load_globals(cls: type) -> list[tuple[str, str]]:
    """Return ``(method, global_name)`` for LOAD_GLOBAL names not in scope."""
    found: list[tuple[str, str]] = []
    for name, obj in vars(cls).items():
        if isinstance(obj, (staticmethod, classmethod)):
            func = obj.__func__
        elif isinstance(obj, types.FunctionType):
            func = obj
        else:
            continue
        for instr in dis.get_instructions(func):
            if instr.opname != 'LOAD_GLOBAL':
                continue
            gname = instr.argval
            if gname in ('None', 'True', 'False'):
                continue
            if gname in func.__globals__ or hasattr(builtins, gname):
                continue
            found.append((name, gname))
    return found


def test_engine_mixins_have_no_missing_globals() -> None:
    problems = []
    for cls in _MIXINS:
        for method, gname in _missing_load_globals(cls):
            problems.append(f'{cls.__name__}.{method} → {gname}')
    assert problems == []
