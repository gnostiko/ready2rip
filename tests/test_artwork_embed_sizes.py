# SPDX-License-Identifier: GPL-3.0-or-later
"""Embed artwork size helpers (no PyGObject)."""

from __future__ import annotations

from ready2rip.rip.encode_dest import should_embed_artwork_for_format
from ready2rip.settings_defs import normalize_artwork_max_size


def test_normalize_artwork_max_size() -> None:
    assert normalize_artwork_max_size(600) == 600
    assert normalize_artwork_max_size(720) == 800
    assert normalize_artwork_max_size(1000) == 800
    assert normalize_artwork_max_size(None) == 600
    assert normalize_artwork_max_size(123) == 600


def test_should_embed_artwork_for_format() -> None:
    assert should_embed_artwork_for_format('mp3', embed_lossy=True, embed_lossless=False)
    assert should_embed_artwork_for_format('aac', embed_lossy=True, embed_lossless=False)
    assert should_embed_artwork_for_format('qaac', embed_lossy=True, embed_lossless=False)
    assert not should_embed_artwork_for_format('flac', embed_lossy=True, embed_lossless=False)
    assert should_embed_artwork_for_format('flac', embed_lossy=False, embed_lossless=True)
    assert should_embed_artwork_for_format('wav', embed_lossy=True, embed_lossless=True)
