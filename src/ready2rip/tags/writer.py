# SPDX-License-Identifier: GPL-3.0-or-later
"""Write tags, ReplayGain, and embedded pictures via mutagen."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from ready2rip.metadata.providers import AlbumMetadata
from ready2rip.paths import track_meta_for

log = logging.getLogger(__name__)


@dataclass
class ReplayGainValues:
    track_gain_db: float
    track_peak: float
    album_gain_db: float | None = None
    album_peak: float | None = None


class TagWriter:
    """Write standard tags, ReplayGain, and embedded pictures."""

    def write_album_tags(
        self,
        path: Path,
        album: AlbumMetadata,
        track_number: int,
        *,
        total_tracks: int | None = None,
    ) -> None:
        suffix = path.suffix.lower()
        track = track_meta_for(album, track_number)
        title = track.title if track and track.title else f'Track {track_number:02d}'
        artist = (
            (track.artist if track and track.artist else None)
            or album.artist
            or 'Unknown Artist'
        )
        album_title = album.title or 'Unknown Album'
        album_artist = album.artist or artist
        date = album.date or ''
        total = total_tracks if total_tracks is not None else (
            len(album.tracks) if album.tracks else None
        )

        if suffix == '.flac':
            self._tag_flac(
                path,
                title=title,
                artist=artist,
                album=album_title,
                album_artist=album_artist,
                date=date,
                track_number=track_number,
                total_tracks=total,
                album_meta=album,
                track_meta=track,
            )
        elif suffix == '.mp3':
            self._tag_mp3(
                path,
                title=title,
                artist=artist,
                album=album_title,
                album_artist=album_artist,
                date=date,
                track_number=track_number,
                total_tracks=total,
                album_meta=album,
                track_meta=track,
            )
        elif suffix in {'.opus', '.ogg'}:
            self._tag_ogg(
                path,
                title=title,
                artist=artist,
                album=album_title,
                album_artist=album_artist,
                date=date,
                track_number=track_number,
                total_tracks=total,
                album_meta=album,
                track_meta=track,
            )
        elif suffix == '.wav':
            self._tag_wav(
                path,
                title=title,
                artist=artist,
                album=album_title,
                album_artist=album_artist,
                date=date,
                track_number=track_number,
                total_tracks=total,
                album_meta=album,
            )
        else:
            log.warning('No tagger for %s', path)

    def write_replaygain(self, path: Path, values: ReplayGainValues) -> None:
        suffix = path.suffix.lower()
        if suffix == '.flac':
            from mutagen.flac import FLAC

            audio = FLAC(path)
            audio['REPLAYGAIN_TRACK_GAIN'] = [f'{values.track_gain_db:.2f} dB']
            audio['REPLAYGAIN_TRACK_PEAK'] = [f'{values.track_peak:.6f}']
            if values.album_gain_db is not None:
                audio['REPLAYGAIN_ALBUM_GAIN'] = [f'{values.album_gain_db:.2f} dB']
            if values.album_peak is not None:
                audio['REPLAYGAIN_ALBUM_PEAK'] = [f'{values.album_peak:.6f}']
            audio.save()
        elif suffix == '.mp3':
            from mutagen.id3 import ID3, TXXX
            from mutagen.mp3 import MP3

            audio = MP3(path, ID3=ID3)
            if audio.tags is None:
                audio.add_tags()
            assert audio.tags is not None
            audio.tags.delall('TXXX:REPLAYGAIN_TRACK_GAIN')
            audio.tags.delall('TXXX:REPLAYGAIN_TRACK_PEAK')
            audio.tags.add(
                TXXX(
                    encoding=3,
                    desc='REPLAYGAIN_TRACK_GAIN',
                    text=[f'{values.track_gain_db:.2f} dB'],
                )
            )
            audio.tags.add(
                TXXX(
                    encoding=3,
                    desc='REPLAYGAIN_TRACK_PEAK',
                    text=[f'{values.track_peak:.6f}'],
                )
            )
            if values.album_gain_db is not None:
                audio.tags.delall('TXXX:REPLAYGAIN_ALBUM_GAIN')
                audio.tags.add(
                    TXXX(
                        encoding=3,
                        desc='REPLAYGAIN_ALBUM_GAIN',
                        text=[f'{values.album_gain_db:.2f} dB'],
                    )
                )
            if values.album_peak is not None:
                audio.tags.delall('TXXX:REPLAYGAIN_ALBUM_PEAK')
                audio.tags.add(
                    TXXX(
                        encoding=3,
                        desc='REPLAYGAIN_ALBUM_PEAK',
                        text=[f'{values.album_peak:.6f}'],
                    )
                )
            audio.save()
        elif suffix in {'.opus', '.ogg'}:
            from mutagen.oggopus import OggOpus

            try:
                audio = OggOpus(path)
            except Exception:  # noqa: BLE001
                from mutagen.oggvorbis import OggVorbis

                audio = OggVorbis(path)
            audio['REPLAYGAIN_TRACK_GAIN'] = [f'{values.track_gain_db:.2f} dB']
            audio['REPLAYGAIN_TRACK_PEAK'] = [f'{values.track_peak:.6f}']
            if values.album_gain_db is not None:
                audio['REPLAYGAIN_ALBUM_GAIN'] = [f'{values.album_gain_db:.2f} dB']
            if values.album_peak is not None:
                audio['REPLAYGAIN_ALBUM_PEAK'] = [f'{values.album_peak:.6f}']
            audio.save()
        else:
            log.debug('ReplayGain not written for %s', path)

    def embed_artwork(
        self,
        path: Path,
        image_bytes: bytes,
        mime: str = 'image/jpeg',
    ) -> None:
        if not image_bytes:
            return
        suffix = path.suffix.lower()
        if suffix == '.flac':
            from mutagen.flac import FLAC, Picture

            audio = FLAC(path)
            audio.clear_pictures()
            pic = Picture()
            pic.type = 3  # front cover
            pic.mime = mime
            pic.desc = 'Cover'
            pic.data = image_bytes
            audio.add_picture(pic)
            audio.save()
        elif suffix == '.mp3':
            from mutagen.id3 import APIC, ID3
            from mutagen.mp3 import MP3

            audio = MP3(path, ID3=ID3)
            if audio.tags is None:
                audio.add_tags()
            assert audio.tags is not None
            audio.tags.delall('APIC')
            audio.tags.add(
                APIC(
                    encoding=3,
                    mime=mime,
                    type=3,
                    desc='Cover',
                    data=image_bytes,
                )
            )
            audio.save()
        elif suffix in {'.opus', '.ogg'}:
            import base64

            from mutagen.flac import Picture
            from mutagen.oggopus import OggOpus

            try:
                audio = OggOpus(path)
            except Exception:  # noqa: BLE001
                from mutagen.oggvorbis import OggVorbis

                audio = OggVorbis(path)
            pic = Picture()
            pic.type = 3
            pic.mime = mime
            pic.desc = 'Cover'
            pic.data = image_bytes
            audio['metadata_block_picture'] = [
                base64.b64encode(pic.write()).decode('ascii')
            ]
            audio.save()
        else:
            log.debug('Artwork not embedded for %s', path)

    def _tag_flac(self, path: Path, **kw) -> None:
        from mutagen.flac import FLAC

        audio = FLAC(path)
        self._apply_vorbis(audio, **kw)
        audio.save()

    def _tag_ogg(self, path: Path, **kw) -> None:
        from mutagen.oggopus import OggOpus

        try:
            audio = OggOpus(path)
        except Exception:  # noqa: BLE001
            from mutagen.oggvorbis import OggVorbis

            audio = OggVorbis(path)
        self._apply_vorbis(audio, **kw)
        audio.save()

    def _apply_vorbis(
        self,
        audio,
        *,
        title: str,
        artist: str,
        album: str,
        album_artist: str,
        date: str,
        track_number: int,
        total_tracks: int | None,
        album_meta: AlbumMetadata | None = None,
        track_meta=None,
    ) -> None:
        audio['TITLE'] = [title]
        audio['ARTIST'] = [artist]
        audio['ALBUM'] = [album]
        audio['ALBUMARTIST'] = [album_artist]
        if date:
            audio['DATE'] = [date]
        if total_tracks:
            audio['TRACKNUMBER'] = [f'{track_number}/{total_tracks}']
            audio['TRACKTOTAL'] = [str(total_tracks)]
        else:
            audio['TRACKNUMBER'] = [str(track_number)]
        # Disc position (Vorbis / FLAC / Opus): always "N/M" (e.g. 1/1).
        disc_num, disc_total = _disc_numbers(album_meta)
        if disc_num is not None:
            total = disc_total if disc_total and disc_total > 0 else 1
            audio['DISCNUMBER'] = [f'{disc_num}/{total}']
            audio['DISCTOTAL'] = [str(total)]
            audio['TOTALDISCS'] = [str(total)]
        if album_meta and album_meta.musicbrainz_release_id:
            audio['MUSICBRAINZ_ALBUMID'] = [album_meta.musicbrainz_release_id]
        if album_meta and album_meta.musicbrainz_release_group_id:
            audio['MUSICBRAINZ_RELEASEGROUPID'] = [
                album_meta.musicbrainz_release_group_id
            ]
        if track_meta is not None and getattr(track_meta, 'musicbrainz_recording_id', ''):
            audio['MUSICBRAINZ_TRACKID'] = [track_meta.musicbrainz_recording_id]
        if album_meta and album_meta.barcode:
            audio['BARCODE'] = [album_meta.barcode]
        if album_meta and album_meta.label:
            audio['LABEL'] = [album_meta.label]
        if album_meta and album_meta.catalog_number:
            audio['CATALOGNUMBER'] = [album_meta.catalog_number]
        if album_meta and album_meta.country:
            audio['RELEASECOUNTRY'] = [album_meta.country]
        if album_meta and album_meta.discid:
            audio['MUSICBRAINZ_DISCID'] = [album_meta.discid]

    def _tag_mp3(
        self,
        path: Path,
        *,
        title: str,
        artist: str,
        album: str,
        album_artist: str,
        date: str,
        track_number: int,
        total_tracks: int | None,
        album_meta: AlbumMetadata | None = None,
        track_meta=None,
    ) -> None:
        from mutagen.id3 import (
            ID3,
            TALB,
            TDRC,
            TIT2,
            TPE1,
            TPE2,
            TPOS,
            TRCK,
            TXXX,
            UFID,
        )
        from mutagen.mp3 import MP3

        audio = MP3(path, ID3=ID3)
        if audio.tags is None:
            audio.add_tags()
        tags = audio.tags
        assert tags is not None
        tags.delall('TIT2')
        tags.delall('TPE1')
        tags.delall('TALB')
        tags.delall('TPE2')
        tags.delall('TRCK')
        tags.delall('TPOS')
        tags.delall('TDRC')
        tags.add(TIT2(encoding=3, text=title))
        tags.add(TPE1(encoding=3, text=artist))
        tags.add(TALB(encoding=3, text=album))
        tags.add(TPE2(encoding=3, text=album_artist))
        if total_tracks:
            tags.add(TRCK(encoding=3, text=f'{track_number}/{total_tracks}'))
        else:
            tags.add(TRCK(encoding=3, text=str(track_number)))
        disc_num, disc_total = _disc_numbers(album_meta)
        if disc_num is not None:
            total = disc_total if disc_total and disc_total > 0 else 1
            tags.add(TPOS(encoding=3, text=f'{disc_num}/{total}'))
        if date:
            tags.add(TDRC(encoding=3, text=date))
        if album_meta and album_meta.musicbrainz_release_id:
            tags.delall('TXXX:MusicBrainz Album Id')
            tags.add(
                TXXX(
                    encoding=3,
                    desc='MusicBrainz Album Id',
                    text=[album_meta.musicbrainz_release_id],
                )
            )
        if track_meta is not None and getattr(track_meta, 'musicbrainz_recording_id', ''):
            tags.delall('UFID:http://musicbrainz.org')
            tags.add(
                UFID(
                    owner='http://musicbrainz.org',
                    data=track_meta.musicbrainz_recording_id.encode('ascii'),
                )
            )
        if album_meta and album_meta.label:
            tags.delall('TXXX:LABEL')
            tags.add(TXXX(encoding=3, desc='LABEL', text=[album_meta.label]))
        audio.save()

    def _tag_wav(
        self,
        path: Path,
        *,
        title: str,
        artist: str,
        album: str,
        album_artist: str,
        date: str,
        track_number: int,
        total_tracks: int | None,
        album_meta: AlbumMetadata | None = None,
    ) -> None:
        try:
            from mutagen.wave import WAVE
        except ImportError:
            log.debug('mutagen WAVE not available')
            return
        audio = WAVE(path)
        if audio.tags is None:
            audio.add_tags()
        # INFO chunk style via mutagen id3-like if present
        try:
            from mutagen.id3 import ID3, TALB, TDRC, TIT2, TPE1, TPE2, TPOS, TRCK

            if not isinstance(audio.tags, ID3):
                return
            tags = audio.tags
            tags.add(TIT2(encoding=3, text=title))
            tags.add(TPE1(encoding=3, text=artist))
            tags.add(TALB(encoding=3, text=album))
            tags.add(TPE2(encoding=3, text=album_artist))
            if total_tracks:
                tags.add(TRCK(encoding=3, text=f'{track_number}/{total_tracks}'))
            else:
                tags.add(TRCK(encoding=3, text=str(track_number)))
            disc_num, disc_total = _disc_numbers(album_meta)
            if disc_num is not None:
                total = disc_total if disc_total and disc_total > 0 else 1
                tags.add(TPOS(encoding=3, text=f'{disc_num}/{total}'))
            if date:
                tags.add(TDRC(encoding=3, text=date))
            audio.save()
        except Exception:  # noqa: BLE001
            log.debug('WAV tagging failed for %s', path, exc_info=True)


def _disc_numbers(
    album_meta: AlbumMetadata | None,
) -> tuple[int | None, int | None]:
    """Return (disc_number, total_discs) for tagging, or (None, None)."""
    if album_meta is None:
        return None, None
    disc = int(album_meta.medium_position or 0)
    total = int(album_meta.medium_count or 0)
    if disc < 1:
        disc = 1
    if total < 1:
        total = 1
    # Always write at least disc 1/1 so players get consistent multi-disc fields.
    return disc, total
