# SPDX-License-Identifier: GPL-3.0-or-later
"""Baseline JPEG encode for embeds / folder covers."""

from __future__ import annotations

import io

from PIL import Image

from ready2rip.artwork.fetch import (
    JPEG_EMBED_QUALITY,
    ArtworkFetcher,
    ArtworkImage,
    encode_jpeg_baseline,
    jpeg_as_baseline,
)


def _rgb_image(size: int = 64, *, color=(40, 80, 120)) -> Image.Image:
    return Image.new('RGB', (size, size), color)


def _is_baseline_jpeg(data: bytes) -> bool:
    """True when the JPEG uses SOF0 (baseline), not SOF2 (progressive)."""
    assert data[:3] == b'\xff\xd8\xff'
    i = 2
    while i < len(data) - 3:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xC0, 0xC2):  # SOF0 baseline / SOF2 progressive
            return marker == 0xC0
        if marker in (0xD8, 0xD9, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        if i + 3 >= len(data):
            break
        length = int.from_bytes(data[i + 2 : i + 4], 'big')
        i += 2 + length
    return False


def test_encode_jpeg_baseline_is_not_progressive() -> None:
    data = encode_jpeg_baseline(_rgb_image(), quality=JPEG_EMBED_QUALITY)
    assert _is_baseline_jpeg(data)
    with Image.open(io.BytesIO(data)) as im:
        assert not im.info.get('progressive')
        assert im.size == (64, 64)


def test_jpeg_as_baseline_strips_progressive() -> None:
    buf = io.BytesIO()
    _rgb_image(128).save(buf, format='JPEG', quality=85, progressive=True)
    progressive = buf.getvalue()
    assert not _is_baseline_jpeg(progressive)

    src = ArtworkImage(
        data=progressive,
        mime='image/jpeg',
        width=128,
        height=128,
        source='test',
    )
    out = jpeg_as_baseline(src)
    assert out.mime == 'image/jpeg'
    assert _is_baseline_jpeg(out.data)
    with Image.open(io.BytesIO(out.data)) as im:
        assert not im.info.get('progressive')


def test_resize_always_reencodes_baseline_q90() -> None:
    """Even when already under max_edge, resize must re-encode baseline Q90."""
    buf = io.BytesIO()
    _rgb_image(200).save(buf, format='JPEG', quality=50, progressive=True)
    src = ArtworkImage(
        data=buf.getvalue(),
        mime='image/jpeg',
        width=200,
        height=200,
        source='test',
    )
    out = ArtworkFetcher().resize(src, 600)
    assert out.width == 200
    assert out.height == 200
    assert _is_baseline_jpeg(out.data)
    # Quality is not stored as a readable field; size should exceed Q50 progressive.
    assert len(out.data) > len(src.data) // 2
