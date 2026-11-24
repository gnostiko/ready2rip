# SPDX-License-Identifier: GPL-3.0-or-later
"""Verify Encoder + Encoded by contracts across codecs (gi/mutagen-light)."""

from __future__ import annotations

from ready2rip.config import APPLICATION_NAME, APPLICATION_VERSION
from ready2rip.metadata.models import AlbumMetadata
from ready2rip.rip.encode import encoder_tag_for_format
from ready2rip.settings_defs import ALSO_ENCODER_IDS, ENCODER_IDS
from ready2rip.tags.writer import (
    TagWriter,
    _apply_id3_encoder_tags,
    ripper_tag_value,
)


def test_ripper_tag_value_shape() -> None:
    assert ripper_tag_value() == f'{APPLICATION_NAME} {APPLICATION_VERSION}'


def test_encoder_tag_covers_every_encode_format() -> None:
    for fmt in sorted(ENCODER_IDS | ALSO_ENCODER_IDS):
        tag = encoder_tag_for_format(fmt)
        assert tag
        assert tag != 'unknown'
        assert 'ready2rip' not in tag.lower()


def test_vorbis_writes_encoded_by() -> None:
    audio: dict[str, list[str]] = {}
    TagWriter()._apply_vorbis(
        audio,
        title='Song',
        artists=['A'],
        album='Album',
        album_artists=['A'],
        track_number=1,
        total_tracks=1,
        album_meta=AlbumMetadata(title='Album', artist='A'),
        encoder='flac 1.4.3',
        encoded_by=ripper_tag_value(),
    )
    assert audio['ENCODER'] == ['flac 1.4.3']
    assert audio['ENCODED-BY'] == [ripper_tag_value()]


def test_id3_writes_encoded_by() -> None:
    import pytest

    pytest.importorskip('mutagen')
    from mutagen.id3 import TENC, TSSE

    class _Tags:
        def __init__(self) -> None:
            self.frames: dict[str, object] = {}

        def delall(self, key: str) -> None:
            self.frames.pop(key, None)

        def add(self, frame) -> None:
            self.frames[frame.FrameID] = frame

    tags = _Tags()
    _apply_id3_encoder_tags(
        tags,
        encoder='LAME 3.100',
        encoded_by=ripper_tag_value(),
    )
    assert isinstance(tags.frames['TSSE'], TSSE)
    assert list(tags.frames['TSSE'].text) == ['LAME 3.100']
    assert isinstance(tags.frames['TENC'], TENC)
    assert list(tags.frames['TENC'].text) == [ripper_tag_value()]


def test_write_album_tags_dispatch_covers_all_suffixes() -> None:
    """Every encode extension has a tagger path (else Encoded by cannot land)."""
    import inspect

    src = inspect.getsource(TagWriter.write_album_tags)
    for suffix in (
        '.flac',
        '.wv',
        '.mp3',
        '.m4a',
        '.mp4',
        '.aac',
        '.opus',
        '.ogg',
        '.wav',
        '.aiff',
        '.aif',
    ):
        assert repr(suffix) in src or f"'{suffix}'" in src


def test_encode_path_always_passes_encoded_by_when_enabled() -> None:
    """Regression: tagging must not be gated solely on album presence."""
    import inspect

    from ready2rip.rip import engine_encode

    src = inspect.getsource(engine_encode.EncodeMixin._encode_wav_to_destinations)
    assert 'encoder_tag_for_format(dest.format)' in src
    assert 'ripper_tag_value() if job.add_ripper_tag else None' in src
    assert 'album_for_tags = job.album or AlbumMetadata' in src
    # Must not skip the whole tag block when album is missing.
    assert 'if job.album is not None:\n                total_for_tags' not in src
