# SPDX-License-Identifier: GPL-3.0-or-later
"""Secure extraction (cdparanoia) and encoding.

Submodules are imported lazily so pure helpers (cue, eac_logcrc) can be
tested without pulling in PyGObject via settings/engine.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    'RipEngine',
    'RipJob',
    'RipLog',
    'RipProgress',
    'RipResult',
    'RipState',
]


def __getattr__(name: str) -> Any:
    if name in {'RipEngine', 'RipJob', 'RipProgress', 'RipResult', 'RipState'}:
        from ready2rip.rip.engine import (
            RipEngine,
            RipJob,
            RipProgress,
            RipResult,
            RipState,
        )

        return {
            'RipEngine': RipEngine,
            'RipJob': RipJob,
            'RipProgress': RipProgress,
            'RipResult': RipResult,
            'RipState': RipState,
        }[name]
    if name == 'RipLog':
        from ready2rip.rip.riplog import RipLog

        return RipLog
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')
