# SPDX-License-Identifier: GPL-3.0-or-later
"""FreeDB/gnudb disc-number heuristics from DTITLE / EXTD."""

from __future__ import annotations

from ready2rip.metadata.providers import (
    _disc_fields_from_cddb_text,
    _parse_cddb_entry,
)


def test_disc_fields_from_title_variants() -> None:
    pos, total, title = _disc_fields_from_cddb_text('Greatest Hits (disc 2)')
    assert (pos, total, title) == (2, 2, 'Greatest Hits')

    pos, total, title = _disc_fields_from_cddb_text('Live [CD 1 of 3]')
    assert (pos, total, title) == (1, 3, 'Live')

    pos, total, title = _disc_fields_from_cddb_text('Box Set (2/3)')
    assert (pos, total, title) == (2, 3, 'Box Set')

    pos, total, title = _disc_fields_from_cddb_text('Plain Album')
    assert (pos, total, title) == (1, 1, 'Plain Album')


def test_disc_fields_from_extd_when_title_clean() -> None:
    pos, total, title = _disc_fields_from_cddb_text(
        'Soundtrack', 'Year: 1999 Disc 2 of 2'
    )
    assert (pos, total, title) == (2, 2, 'Soundtrack')


def test_parse_cddb_entry_sets_medium_fields() -> None:
    body = (
        'DISCID=a10b0c0d\n'
        'DTITLE=Artist / Album Title (disc 2)\n'
        'DYEAR=1999\n'
        'DGENRE=Rock\n'
        'TTITLE0=One\n'
        'TTITLE1=Two\n'
        'EXTD=CD 2 of 2\n'
    )
    album = _parse_cddb_entry(body, source='gnudb', category='rock')
    assert album is not None
    assert album.title == 'Album Title'
    assert album.artist == 'Artist'
    assert album.medium_position == 2
    assert album.medium_count == 2
