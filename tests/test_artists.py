# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for multi-artist formatting styles."""

from __future__ import annotations

from ready2rip.tags.artists import (
    ARTIST_STYLE_FEAT,
    ARTIST_STYLE_SEMICOLON,
    ARTIST_STYLE_SLASH,
    format_artists,
    format_title_with_artists,
    join_names_natural,
    normalize_artists,
    primary_artist,
    split_artists,
)


def test_split_and_normalize() -> None:
    assert split_artists('A; B; C') == ['A', 'B', 'C']
    assert normalize_artists(' A;B ;; C ') == 'A; B; C'


def test_join_names_natural() -> None:
    assert join_names_natural(['A']) == 'A'
    assert join_names_natural(['A', 'B']) == 'A & B'
    assert join_names_natural(['A', 'B', 'C']) == 'A, B & C'
    assert join_names_natural(['A', 'B', 'C', 'D']) == 'A, B, C & D'


def test_format_artists_styles() -> None:
    value = 'Main; Guest; Other'
    assert format_artists(value, ARTIST_STYLE_SEMICOLON) == 'Main; Guest; Other'
    assert format_artists(value, ARTIST_STYLE_SLASH) == 'Main / Guest / Other'
    assert format_artists(value, ARTIST_STYLE_FEAT) == 'Main feat. Guest & Other'
    assert format_artists('Solo', ARTIST_STYLE_FEAT) == 'Solo'


def test_format_title_with_artists_feat() -> None:
    assert (
        format_title_with_artists('Song', 'Main; Guest; Other', ARTIST_STYLE_FEAT)
        == 'Song feat. Guest & Other'
    )
    assert (
        format_title_with_artists('Song', 'Main; Guest', ARTIST_STYLE_FEAT)
        == 'Song feat. Guest'
    )
    assert (
        format_title_with_artists('Song', 'Main; A; B; C', ARTIST_STYLE_FEAT)
        == 'Song feat. A, B & C'
    )
    # Non-feat styles leave the title alone.
    assert (
        format_title_with_artists('Song', 'Main; Guest', ARTIST_STYLE_SLASH) == 'Song'
    )
    # Already feat. in title → leave alone.
    assert (
        format_title_with_artists(
            'Song feat. Someone', 'Main; Guest', ARTIST_STYLE_FEAT
        )
        == 'Song feat. Someone'
    )


def test_primary_artist() -> None:
    assert primary_artist('Main; Guest') == 'Main'
    assert primary_artist('') == ''
