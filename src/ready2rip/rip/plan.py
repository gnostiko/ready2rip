# SPDX-License-Identifier: GPL-3.0-or-later
"""Encode destinations and output path planning (gi-free)."""

from __future__ import annotations

import shutil
from dataclasses import dataclass, replace
from pathlib import Path

from ready2rip.paths import unique_output_path
from ready2rip.rip.encode_dest import format_folder_name, is_lossless_extension
from ready2rip.settings_defs import normalize_existing_files_policy

# Codec id → file extension (shared by engine + planners).
ENCODE_EXTENSIONS: dict[str, str] = {
    'flac': '.flac',
    'wavpack': '.wv',
    'alac': '.m4a',
    'mp3': '.mp3',
    'aac': '.m4a',
    'qaac': '.m4a',
    'opus': '.opus',
    'wav': '.wav',
    'aiff': '.aiff',
}


@dataclass(frozen=True)
class EncodeDest:
    """One encode destination resolved to album folder + output file."""

    format: str
    base_dir: Path
    # Directory that holds this format's audio files.
    album_dir: Path
    out_path: Path
    # Album root for cover / log / CUE (may be the parent of album_dir).
    meta_dir: Path
    primary: bool = False

    @property
    def extension(self) -> str:
        return ENCODE_EXTENSIONS[self.format]


def apply_existing_files_policy(
    dests: list[EncodeDest],
    policy: str | None,
) -> list[EncodeDest]:
    """Filter or rename destinations when output files already exist.

    ``ask`` is resolved in the UI to ``overwrite`` (or cancel) before run.
    """
    mode = normalize_existing_files_policy(policy)
    if mode in ('overwrite', 'ask'):
        return dests
    resolved: list[EncodeDest] = []
    for dest in dests:
        if not dest.out_path.exists():
            resolved.append(dest)
            continue
        if mode == 'skip':
            continue
        if mode == 'version':
            resolved.append(replace(dest, out_path=unique_output_path(dest.out_path)))
    return resolved


def conflicting_paths(paths: list[Path]) -> list[Path]:
    """Return existing files from *paths* (stable order)."""
    return [p for p in paths if p.exists()]


def place_wav_output(src: Path, dest: Path, *, move: bool) -> None:
    """Write *src* to *dest*; rename when safe, else copy."""
    if dest.exists():
        dest.unlink()
    if move:
        try:
            src.replace(dest)
            return
        except OSError:
            pass
    shutil.copy2(src, dest)


def unique_album_dirs(outputs: list[Path], album_dir: Path | None) -> list[Path]:
    """Lossless album folders that should receive data-track .bin files.

    Lossy companion folders (MP3 / M4A / Opus) are omitted so ``.bin`` stays
    with the primary lossless rip only.
    """
    dirs: list[Path] = []
    seen: set[Path] = set()
    if album_dir is not None:
        dirs.append(album_dir)
        seen.add(album_dir)
    skip = {'.cue', '.log', '.jpg', '.jpeg', '.png', '.bin'}
    # AAC/QAAC also-encode folders share ``.m4a`` with ALAC — skip by name.
    lossy_folders = {format_folder_name(f) for f in ('mp3', 'aac', 'qaac', 'opus')}
    for path in outputs:
        suffix = path.suffix.lower()
        if suffix in skip:
            continue
        parent = path.parent
        if parent.name in lossy_folders:
            continue
        if not is_lossless_extension(suffix) and suffix != '.m4a':
            continue
        if parent in seen:
            continue
        seen.add(parent)
        dirs.append(parent)
    return dirs
