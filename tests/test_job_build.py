# SPDX-License-Identifier: GPL-3.0-or-later
"""job_from_settings mapping (gi-free)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from ready2rip.rip.job_build import job_from_settings


def _settings(**overrides: object) -> SimpleNamespace:
    """Minimal AppSettings stand-in that does not import PyGObject."""
    values = {
        'device': '/dev/sr0',
        'output_directory': '/tmp/music',
        'encode_format': 'flac',
        'flac_compression': 5,
        'wavpack_compression': 'high',
        'mp3_quality': 'v0',
        'aac_quality': 'cbr256',
        'aac_bitrate': 256,
        'qaac_quality': 'cvbr256',
        'opus_bitrate': 160,
        'also_encode': False,
        'also_encode_format': 'mp3',
        'also_encode_folder': 'format_subdir',
        'also_encode_directory': '',
        'apply_replaygain': True,
        'write_sound_check': True,
        'add_ripper_tag': True,
        'embed_lyrics': True,
        'lyrics_save_mode': 'both',
        'embed_artwork': True,
        'embed_artwork_lossless': False,
        'filename_template': '{track:02d} - {title}',
        'album_folder_template': '{album_artist} - {album}',
        'artist_style': 'semicolon',
        'm4a_feat_artists': True,
        'verify_accuraterip': True,
        'require_db_match': False,
        'archival_strict': False,
        'drive_sample_offset': 0,
        'drive_offset_configured': False,
        'rip_mode': 'archival',
        'burst_fallback': False,
        'write_rip_log': True,
        'write_cue_file': True,
        'rip_htoa': True,
        'rip_data_tracks': True,
        'copy_image': False,
        'artwork_max_size': 600,
        'artwork_max_size_lossless': 600,
        'existing_files': 'overwrite',
    }
    values.update(overrides)
    snap = SimpleNamespace(**values)

    def resolved_output_directory() -> Path:
        return Path(str(snap.output_directory)).expanduser()

    def resolved_also_encode_directory() -> Path:
        raw = str(snap.also_encode_directory or '').strip()
        if raw:
            return Path(raw).expanduser()
        return resolved_output_directory()

    snap.resolved_output_directory = resolved_output_directory  # type: ignore[attr-defined]
    snap.resolved_also_encode_directory = resolved_also_encode_directory  # type: ignore[attr-defined]
    return snap


def test_job_from_settings_derives_extract_flags() -> None:
    settings = _settings(rip_mode='normal', burst_fallback=True)
    job = job_from_settings(settings, track_numbers=[1, 2])
    assert job.rip_mode == 'normal'
    assert job.burst_mode is False
    assert job.test_and_copy is False
    # Fallback only applies in archival.
    assert job.burst_fallback is False


def test_job_from_settings_archival_burst_fallback() -> None:
    settings = _settings(rip_mode='archival', burst_fallback=True)
    job = job_from_settings(settings, track_numbers=[1])
    assert job.test_and_copy is True
    assert job.burst_fallback is True


def test_job_from_settings_also_custom_folder() -> None:
    settings = _settings(
        also_encode=True,
        also_encode_folder='custom_folder',
        also_encode_directory='/tmp/lossy',
        output_directory='/tmp/music',
    )
    job = job_from_settings(settings, track_numbers=[1])
    assert job.also_encode_directory == '/tmp/lossy'
