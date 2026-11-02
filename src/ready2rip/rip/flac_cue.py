# SPDX-License-Identifier: GPL-3.0-or-later
"""Embed a FLAC CUESHEET block from disc TOC / album metadata."""

from __future__ import annotations

import logging
import struct
from pathlib import Path

from ready2rip.disc.probe import DiscInfo
from ready2rip.metadata.providers import AlbumMetadata
from ready2rip.paths import track_meta_for

log = logging.getLogger(__name__)


def embed_flac_cuesheet(
    flac_path: Path,
    *,
    disc: DiscInfo,
    album: AlbumMetadata | None,
    file_start_sector: int = 0,
    htoa_index00: bool = False,
) -> bool:
    """Write a native FLAC CUESHEET matching *disc* into *flac_path*.

    Returns True on success. Non-FLAC paths are ignored.
    """
    if flac_path.suffix.lower() != '.flac':
        return False
    try:
        from mutagen.flac import FLAC, CueSheet, CueSheetTrack, CueSheetTrackIndex
    except ImportError:
        log.debug('mutagen FLAC CueSheet unavailable')
        return False

    try:
        audio = FLAC(str(flac_path))
    except Exception as exc:
        log.warning('Could not open FLAC for cuesheet: %s', exc)
        return False

    leadout = 0
    for t in disc.tracks:
        end = int(t.start_sector) + int(t.length_sectors)
        if end > leadout:
            leadout = end
    if int(getattr(disc, 'leadout_lba', 0) or 0) > leadout:
        leadout = int(disc.leadout_lba)

    catalog = ''
    if album is not None:
        catalog = ((album.mcn or album.barcode) or '').strip()
    catalog_b = catalog.encode('ascii', errors='ignore')[:128]

    try:
        # Empty compact-disc cuesheet header (0 tracks); we fill tracks below.
        header = struct.pack('>128sQB258xB', catalog_b, 88200, 0x80, 0)
        cs = CueSheet(header)
        cs.media_catalog_number = catalog_b
        cs.lead_in_samples = 88200
        cs.compact_disc = True
        tracks: list = []
        for t in disc.tracks:
            rel = int(t.start_sector) - int(file_start_sector)
            if rel < 0:
                rel = 0
            isrc = ''
            if album is not None:
                meta = track_meta_for(album, t.number)
                if meta is not None:
                    isrc = (getattr(meta, 'isrc', '') or '').strip()[:12]
            ct = CueSheetTrack(t.number, rel * 588, isrc=isrc)
            if (
                htoa_index00
                and t.number == 1
                and int(t.start_sector) > int(file_start_sector)
            ):
                ct.indexes.append(CueSheetTrackIndex(0, 0))
            ct.indexes.append(CueSheetTrackIndex(1, rel * 588))
            tracks.append(ct)
        lo_rel = leadout - int(file_start_sector)
        if lo_rel < 0:
            lo_rel = 0
        lo = CueSheetTrack(170, lo_rel * 588)
        lo.indexes.append(CueSheetTrackIndex(1, lo_rel * 588))
        tracks.append(lo)
        cs.tracks = tracks
        audio.cuesheet = cs
        audio.save()
        return True
    except Exception as exc:
        log.warning('FLAC cuesheet embed failed on %s: %s', flac_path, exc)
        return False
