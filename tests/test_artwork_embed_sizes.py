# SPDX-License-Identifier: GPL-3.0-or-later
"""Embed artwork size helpers (no GTK)."""

from __future__ import annotations

from ready2rip.rip.engine import EmbedArtworkSet, RipJob
from ready2rip.settings import normalize_artwork_max_size


def test_normalize_artwork_max_size() -> None:
    assert normalize_artwork_max_size(600) == 600
    assert normalize_artwork_max_size(720) == 800
    assert normalize_artwork_max_size(1000) == 800
    assert normalize_artwork_max_size(None) == 600
    assert normalize_artwork_max_size(123) == 600


def test_embed_artwork_set_picks_by_format() -> None:
    class _Img:
        def __init__(self, name: str) -> None:
            self.name = name

    lossy = _Img('lossy')
    lossless = _Img('lossless')
    arts = EmbedArtworkSet(lossy=lossy, lossless=lossless)  # type: ignore[arg-type]
    job = RipJob(
        device='/dev/sr0',
        track_numbers=[1],
        output_directory='/tmp',
        embed_artwork=True,
        embed_artwork_lossless=True,
    )
    assert arts.for_format('mp3', job) is lossy
    assert arts.for_format('aac', job) is lossy
    assert arts.for_format('qaac', job) is lossy
    assert arts.for_format('flac', job) is lossless
    assert arts.for_format('wav', job) is lossless

    job_lossy_only = RipJob(
        device='/dev/sr0',
        track_numbers=[1],
        output_directory='/tmp',
        embed_artwork=True,
        embed_artwork_lossless=False,
    )
    assert arts.for_format('flac', job_lossy_only) is None
    assert arts.for_format('mp3', job_lossy_only) is lossy
