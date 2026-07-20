# SPDX-License-Identifier: GPL-3.0-or-later
"""Online metadata providers (MusicBrainz, FreeDB/gnudb).

Import cache from ``ready2rip.metadata.cache`` separately to avoid cycles with
artwork packages.
"""

from ready2rip.metadata.providers import (
    AlbumMetadata,
    FreeDBProvider,
    MetadataProvider,
    MusicBrainzProvider,
    TrackMetadata,
    lookup_metadata,
)

__all__ = [
    'AlbumMetadata',
    'FreeDBProvider',
    'MetadataProvider',
    'MusicBrainzProvider',
    'TrackMetadata',
    'lookup_metadata',
]
