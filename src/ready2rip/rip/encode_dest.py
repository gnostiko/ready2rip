# SPDX-License-Identifier: GPL-3.0-or-later
"""Resolve primary + optional secondary encode destinations (gi-free)."""

from __future__ import annotations

from pathlib import Path

_ALSO_ENCODER_IDS = frozenset({'mp3', 'aac', 'opus'})
_FOLDER_LAYOUT_IDS = frozenset({'format_subdir', 'same_folder', 'custom_folder'})

# Formats / extensions that may receive a .cue sheet (lossless / PCM only).
LOSSLESS_FORMATS = frozenset({'flac', 'wav', 'aiff'})
LOSSLESS_EXTENSIONS = frozenset({'.flac', '.wav', '.aiff', '.aif'})
LOSSY_FORMATS = frozenset({'mp3', 'aac', 'opus'})

_FORMAT_FOLDER_NAMES = {
    'flac': 'FLAC',
    'wav': 'WAV',
    'aiff': 'AIFF',
    'mp3': 'MP3',
    'aac': 'M4A',
    'opus': 'Opus',
}


def is_lossless_format(fmt: str) -> bool:
    return (fmt or '').strip().lower() in LOSSLESS_FORMATS


def is_lossy_format(fmt: str) -> bool:
    return (fmt or '').strip().lower() in LOSSY_FORMATS


def normalize_encode_format(value: str | None) -> str:
    """Primary format must be lossless; legacy lossy settings migrate to FLAC."""
    raw = (value or '').strip().lower()
    if raw in LOSSLESS_FORMATS:
        return raw
    return 'flac'


def should_embed_artwork_for_format(
    fmt: str, *, embed_lossy: bool, embed_lossless: bool
) -> bool:
    """Whether *fmt* should receive an embedded cover."""
    key = (fmt or '').strip().lower()
    if is_lossy_format(key):
        return bool(embed_lossy)
    if is_lossless_format(key):
        return bool(embed_lossless)
    return False


def is_lossless_extension(ext: str) -> bool:
    raw = (ext or '').strip().lower()
    if raw and not raw.startswith('.'):
        raw = f'.{raw}'
    return raw in LOSSLESS_EXTENSIONS


def format_folder_name(fmt: str) -> str:
    """Album subfolder name for *fmt* when using Subfolder by format."""
    key = (fmt or '').strip().lower()
    return _FORMAT_FOLDER_NAMES.get(key, key.upper() or 'Audio')


def encode_format_list(
    primary: str,
    *,
    also_encode: bool = False,
    also_format: str = 'mp3',
    allow_also: bool = True,
) -> list[str]:
    """Ordered encode formats for one rip (primary first).

    Secondary is skipped when disabled, disallowed (e.g. Copy Image), identical
    to primary, or not a supported also-encode codec.
    """
    primary_fmt = (primary or 'flac').strip().lower() or 'flac'
    if primary_fmt not in LOSSLESS_FORMATS:
        primary_fmt = 'flac'
    formats = [primary_fmt]
    if not also_encode or not allow_also:
        return formats
    secondary = (also_format or 'mp3').strip().lower()
    if secondary not in _ALSO_ENCODER_IDS:
        secondary = 'mp3'
    # Primary is always lossless; secondary is always lossy — never equal.
    formats.append(secondary)
    return formats


def audio_dir_for_format(
    album_dir: Path,
    fmt: str,
    *,
    folder_layout: str = 'format_subdir',
    multi_format: bool = False,
) -> Path:
    """Directory that holds audio for *fmt* under *album_dir*."""
    layout = (folder_layout or 'format_subdir').strip().lower()
    if layout not in _FOLDER_LAYOUT_IDS:
        layout = 'format_subdir'
    # custom_folder uses a separate output root (caller); files sit in the album dir.
    if multi_format and layout == 'format_subdir':
        return album_dir / format_folder_name(fmt)
    return album_dir
