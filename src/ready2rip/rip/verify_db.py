# SPDX-License-Identifier: GPL-3.0-or-later
"""Compatibility shim — use :mod:`ready2rip.rip.job_extract` instead."""

from __future__ import annotations

from ready2rip.rip.job_extract import job_extract_mode, sync_job_extract_flags

__all__ = ['job_extract_mode', 'sync_job_extract_flags']
