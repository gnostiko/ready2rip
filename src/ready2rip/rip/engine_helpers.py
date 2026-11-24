# SPDX-License-Identifier: GPL-3.0-or-later
"""Small helpers used by RipEngine mixins (gi-free)."""

from __future__ import annotations

import logging
from pathlib import Path

from ready2rip.disc.probe import DiscInfo

log = logging.getLogger('ready2rip.rip.engine')

def _retag_title(path: Path, title: str, *, track_number: int = 0) -> None:
    """Overwrite title / track number for HTOA after generic album tags."""
    try:
        suffix = path.suffix.lower()
        if suffix == '.flac':
            from mutagen.flac import FLAC

            audio = FLAC(path)
            audio['TITLE'] = [title]
            audio['TRACKNUMBER'] = [str(track_number)]
            audio.save()
        elif suffix == '.wv':
            from mutagen.wavpack import WavPack

            audio = WavPack(path)
            audio['TITLE'] = [title]
            audio['TRACKNUMBER'] = [str(track_number)]
            audio.save()
        elif suffix == '.mp3':
            from mutagen.id3 import ID3, TIT2, TRCK
            from mutagen.mp3 import MP3

            audio = MP3(path, ID3=ID3)
            if audio.tags is None:
                audio.add_tags()
            tags = audio.tags
            if tags is None:
                return
            tags.add(TIT2(encoding=3, text=title))
            tags.add(TRCK(encoding=3, text=str(track_number)))
            audio.save()
        elif suffix in {'.m4a', '.mp4', '.aac'}:
            from mutagen.mp4 import MP4

            audio = MP4(path)
            audio['\xa9nam'] = [title]
            audio['trkn'] = [(track_number, 0)]
            audio.save()
        elif suffix in {'.opus', '.ogg'}:
            from mutagen.oggopus import OggOpus
            from mutagen.oggvorbis import OggVorbis

            try:
                audio = OggOpus(path)
            except Exception:
                audio = OggVorbis(path)
            audio['TITLE'] = [title]
            audio['TRACKNUMBER'] = [str(track_number)]
            audio.save()
    except Exception as exc:
        log.warning('Could not retag HTOA title on %s: %s', path, exc)


def _expected_wav_sizes(info: DiscInfo | None) -> dict[int, int]:
    """Map track number → approximate WAV byte size (44-byte header + PCM)."""
    if info is None:
        return {}
    sizes: dict[int, int] = {}
    for track in info.tracks:
        pcm = track.length_sectors * 2352
        sizes[track.number] = 44 + pcm
    return sizes


def _extract_timeout(expected_bytes: int | None, *, mode: str) -> int:
    """Wall-clock budget for one track extract based on audio length."""
    # CDDA stereo 16-bit 44.1 kHz = 176400 bytes/sec of PCM (+44 WAV header).
    if expected_bytes and expected_bytes > 1000:
        audio_sec = max(1.0, (expected_bytes - 44) / 176400.0)
    else:
        audio_sec = 300.0
    if mode in ('secure', 'normal', 'archival'):
        # Full / repair paranoia; allow up to ~10× realtime, min 10 min, max 90 min.
        return int(min(5400, max(600, audio_sec * 10 + 120)))
    # Burst (-Z) is faster; still bound by slow drives.
    return int(min(2400, max(300, audio_sec * 4 + 60)))


def _track_avoid_span(disc: DiscInfo | None, track_no: int) -> tuple[int | None, int | None]:
    """Absolute ``[start, end)`` for the track just extracted (cache flush)."""
    if disc is None:
        return None, None
    for t in disc.tracks:
        if t.number == track_no:
            start = int(t.start_sector)
            return start, start + int(t.length_sectors)
    return None, None

