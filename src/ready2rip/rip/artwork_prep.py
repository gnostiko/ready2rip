# SPDX-License-Identifier: GPL-3.0-or-later
"""Folder cover and tag embed artwork preparation for rip jobs."""

from __future__ import annotations

import logging
from pathlib import Path

from ready2rip.artwork.fetch import ArtworkFetcher, ArtworkImage
from ready2rip.rip.engine_types import EmbedArtworkSet, RipJob

log = logging.getLogger(__name__)


def scaled_embed(source: ArtworkImage | None, max_edge: int) -> ArtworkImage | None:
    """Return *source* as baseline JPEG Q90, scaled to at most *max_edge*."""
    if source is None:
        return None
    # Always re-encode so progressive source JPEGs never land in tags.
    return ArtworkFetcher().resize(source, max_edge)


def prepare_artwork(
    job: RipJob,
    album_dir: Path,
) -> tuple[EmbedArtworkSet, ArtworkImage | None, Path | None, list[str]]:
    """Save full-size cover to the album folder; return lossy/lossless embeds.

    Called **before** track extraction begins. Folder cover keeps the original
    downloaded bytes (no re-encode). Tag embeds are baseline JPEG Q90.
    """

    notes: list[str] = []
    candidates = [
        img for img in (job.folder_artwork, job.artwork, job.artwork_lossless) if img is not None
    ]
    folder_art = max(candidates, key=lambda img: img.max_edge) if candidates else None

    cover_path: Path | None = None
    embeds = EmbedArtworkSet()
    if job.save_cover_file and folder_art is not None and folder_art.data:
        if folder_art.mime == 'image/png':
            cover_path = album_dir / 'cover.png'
        else:
            cover_path = album_dir / 'cover.jpg'
        try:
            # Write source bytes as-is — do not re-encode folder cover.
            cover_path.write_bytes(folder_art.data)
            notes.append(
                f'Folder cover written '
                f'({folder_art.width}×{folder_art.height} {folder_art.source})'
            )
        except OSError as exc:
            log.warning('Could not write cover file: %s', exc)
            notes.append(f'Could not write cover: {exc}')
            cover_path = None

    embeds = embed_set_from_job(job, folder_art)
    if embeds.lossy is not None:
        notes.append(f'Lossy embed artwork ready ({embeds.lossy.width}×{embeds.lossy.height})')
    if embeds.lossless is not None:
        notes.append(
            f'Lossless embed artwork ready ({embeds.lossless.width}×{embeds.lossless.height})'
        )

    return embeds, folder_art, cover_path, notes


def embed_set_from_job(job: RipJob, folder_art: ArtworkImage | None) -> EmbedArtworkSet:
    """Build lossy/lossless embed images from job fields + folder source."""
    lossy: ArtworkImage | None = None
    lossless: ArtworkImage | None = None
    if job.embed_artwork:
        lossy = scaled_embed(
            job.artwork or folder_art,
            int(job.artwork_max_size),
        )
    if job.embed_artwork_lossless:
        lossless = scaled_embed(
            job.artwork_lossless or folder_art,
            int(job.artwork_max_size_lossless),
        )
    return EmbedArtworkSet(lossy=lossy, lossless=lossless)