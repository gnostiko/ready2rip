# SPDX-License-Identifier: GPL-3.0-or-later
"""Audio tagging and ReplayGain."""

from ready2rip.tags.artists import (
    ARTIST_SEPARATOR,
    join_artists,
    normalize_artists,
    split_artists,
)
from ready2rip.tags.writer import ReplayGainValues, TagWriter

__all__ = [
    'ARTIST_SEPARATOR',
    'ReplayGainValues',
    'TagWriter',
    'join_artists',
    'normalize_artists',
    'split_artists',
]
