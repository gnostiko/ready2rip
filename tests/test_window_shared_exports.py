# SPDX-License-Identifier: GPL-3.0-or-later
"""UI mixin star-imports must resolve every LOAD_GLOBAL from ``_window_shared``."""

from __future__ import annotations

import builtins
import dis
import types

import pytest

gi = pytest.importorskip('gi')
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from ready2rip.ui.window_drive import DriveMonitorMixin
from ready2rip.ui.window_metadata import MetadataActionsMixin
from ready2rip.ui.window_rip import RipActionsMixin

_MIXINS = (RipActionsMixin, DriveMonitorMixin, MetadataActionsMixin)


def _missing_load_globals(cls: type) -> list[tuple[str, str]]:
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


def test_window_mixins_have_no_missing_globals() -> None:
    problems = []
    for cls in _MIXINS:
        for method, gname in _missing_load_globals(cls):
            problems.append(f'{cls.__name__}.{method} → {gname}')
    assert problems == []
