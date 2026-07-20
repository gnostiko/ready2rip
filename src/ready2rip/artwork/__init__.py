# SPDX-License-Identifier: GPL-3.0-or-later
"""Album artwork download and resize.

Import concrete symbols from ``ready2rip.artwork.fetch`` to avoid package-level
import cycles with metadata.
"""

__all__ = [
    'ArtworkFetcher',
    'ArtworkImage',
    'ArtworkSourceOptions',
    'apply_artwork_to_image',
    'pixbuf_from_artwork',
]


def __getattr__(name: str):
    if name in __all__:
        from ready2rip.artwork import fetch as _fetch

        return getattr(_fetch, name)
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')
