# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for multi-artist formatting styles."""

from __future__ import annotations

from ready2rip.tags.artists import (
    ARTIST_STYLE_FEAT,
    ARTIST_STYLE_SEMICOLON,
    ARTIST_STYLE_SLASH,
    artist_style_for_container,
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


def test_format_title_with_artists_leaves_title_clean() -> None:
    # Guests belong on the artist string; title is never rewritten.
    assert (
        format_title_with_artists('Song', 'Main; Guest; Other', ARTIST_STYLE_FEAT)
        == 'Song'
    )
    assert format_title_with_artists('Song', 'Main; Guest', ARTIST_STYLE_SLASH) == 'Song'
    assert (
        format_title_with_artists(
            'Song feat. Someone', 'Main; Guest', ARTIST_STYLE_FEAT
        )
        == 'Song feat. Someone'
    )


def test_primary_artist() -> None:
    assert primary_artist('Main; Guest') == 'Main'
    assert primary_artist('') == ''


def test_artist_style_for_container_m4a_override() -> None:
    assert (
        artist_style_for_container(
            ARTIST_STYLE_SEMICOLON,
            suffix='.m4a',
            m4a_feat_artists=True,
        )
        == ARTIST_STYLE_FEAT
    )
    assert (
        artist_style_for_container(
            ARTIST_STYLE_SLASH,
            encode_format='qaac',
            m4a_feat_artists=True,
        )
        == ARTIST_STYLE_FEAT
    )
    assert (
        artist_style_for_container(
            ARTIST_STYLE_SEMICOLON,
            suffix='.flac',
            encode_format='flac',
            m4a_feat_artists=True,
        )
        == ARTIST_STYLE_SEMICOLON
    )
    assert (
        artist_style_for_container(
            ARTIST_STYLE_SLASH,
            suffix='.m4a',
            m4a_feat_artists=False,
        )
        == ARTIST_STYLE_SLASH
    )
