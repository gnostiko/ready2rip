# SPDX-License-Identifier: GPL-3.0-or-later
"""Extract-mode helpers: rip-mode normalization and job flag alignment."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ready2rip.settings_defs import derive_extract_flags, normalize_rip_mode

if TYPE_CHECKING:
    from ready2rip.rip.engine_types import RipJob


def job_extract_mode(job: RipJob) -> str:
    """paranoia mode token: ``burst`` | ``normal`` | ``secure``."""
    mode = normalize_rip_mode(job.rip_mode)
    if job.burst_mode or mode == 'burst':
        return 'burst'
    if mode == 'normal':
        return 'normal'
    return 'secure'


def sync_job_extract_flags(job: RipJob) -> None:
    """Align ``burst_mode`` / ``test_and_copy`` with ``rip_mode`` on *job*."""
    mode, burst, tac = derive_extract_flags(job.rip_mode)
    job.rip_mode = mode
    job.burst_mode = burst
    job.test_and_copy = tac
