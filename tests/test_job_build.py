# SPDX-License-Identifier: GPL-3.0-or-later
"""job_from_settings mapping."""

from __future__ import annotations

from ready2rip.rip.job_build import job_from_settings
from ready2rip.settings import AppSettings


def test_job_from_settings_derives_extract_flags() -> None:
    settings = AppSettings(rip_mode='normal', burst_fallback=True)
    job = job_from_settings(settings, track_numbers=[1, 2])
    assert job.rip_mode == 'normal'
    assert job.burst_mode is False
    assert job.test_and_copy is False
    # Fallback only applies in archival.
    assert job.burst_fallback is False


def test_job_from_settings_archival_burst_fallback() -> None:
    settings = AppSettings(rip_mode='archival', burst_fallback=True)
    job = job_from_settings(settings, track_numbers=[1])
    assert job.test_and_copy is True
    assert job.burst_fallback is True


def test_job_from_settings_also_custom_folder() -> None:
    settings = AppSettings(
        also_encode=True,
        also_encode_folder='custom_folder',
        also_encode_directory='/tmp/lossy',
        output_directory='/tmp/music',
    )
    job = job_from_settings(settings, track_numbers=[1])
    assert job.also_encode_directory == '/tmp/lossy'
