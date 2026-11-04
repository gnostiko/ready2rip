# SPDX-License-Identifier: GPL-3.0-or-later
"""Write tags, ReplayGain, and embedded pictures via mutagen.

Format conventions (Picard-aligned):
  * Vorbis (FLAC / Opus) — one ARTIST/ALBUMARTIST field per name;
    TRACKNUMBER/DISCNUMBER are bare integers with separate *TOTAL fields;
    recording UUID goes in MUSICBRAINZ_RECORDINGID.
  * ID3 (MP3 / WAV) — multi-value TPE1/TPE2; TRCK/TPOS use ``N/M``;
    recording UUID in UFID ``http://musicbrainz.org``.
  * MP4 (M4A) — multi-value ``©ART`` / ``aART``; ``trkn`` / ``disk`` tuples;
    Picard freeform atoms for MusicBrainz ids (Track Id = recording).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from ready2rip.metadata.providers import AlbumMetadata, TrackMetadata
from ready2rip.paths import track_meta_for
from ready2rip.tags.artists import (
    ARTIST_STYLE_FEAT,
    DEFAULT_ARTIST_STYLE,
    format_title_with_artists,
    normalize_artist_style,
    normalize_artists,
    primary_artist,
    split_artists,
)
from ready2rip.util import normalize_release_date

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
        artist_style: str | None = DEFAULT_ARTIST_STYLE,
    ) -> None:
        suffix = path.suffix.lower()
        style = normalize_artist_style(artist_style)
        track = track_meta_for(album, track_number)
        raw_title = (
            track.title if track and track.title else f'Track {track_number:02d}'
        )
        # UI / paths keep "A; B; C"; tags use native multi-value (or feat. title).
        raw_artist = normalize_artists(
            (track.artist if track and track.artist else None)
            or album.artist
            or 'Unknown Artist'
        )
        album_title = album.title or 'Unknown Album'
        raw_album_artist = normalize_artists(album.artist or raw_artist)

        if style == ARTIST_STYLE_FEAT:
            title = format_title_with_artists(raw_title, raw_artist, style)
        else:
            title = raw_title
        artists = _artist_names_for_tags(raw_artist, style=style)
        album_artists = _artist_names_for_tags(raw_album_artist, style=style)

        date = normalize_release_date(album.date or '')
        total = total_tracks if total_tracks is not None else (
            len(album.tracks) if album.tracks else None
        )
        kw = dict(
            title=title,
            artists=artists,
            album=album_title,
            album_artists=album_artists,
            date=date,
            track_number=track_number,
            total_tracks=total,
            album_meta=album,
            track_meta=track,
        )

        if suffix == '.flac':
            self._tag_flac(path, **kw)
        elif suffix == '.mp3':
            self._tag_mp3(path, **kw)
        elif suffix in {'.m4a', '.mp4', '.aac'}:
            self._tag_mp4(path, **kw)
        elif suffix in {'.opus', '.ogg'}:
            self._tag_ogg(path, **kw)
        elif suffix == '.wav':
            self._tag_wav(path, **kw)
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
        elif suffix in {'.m4a', '.mp4', '.aac'}:
            from mutagen.mp4 import MP4

            audio = MP4(path)
            audio['----:com.apple.iTunes:REPLAYGAIN_TRACK_GAIN'] = [
                f'{values.track_gain_db:.2f} dB'.encode()
            ]
            audio['----:com.apple.iTunes:REPLAYGAIN_TRACK_PEAK'] = [
                f'{values.track_peak:.6f}'.encode()
            ]
            if values.album_gain_db is not None:
                audio['----:com.apple.iTunes:REPLAYGAIN_ALBUM_GAIN'] = [
                    f'{values.album_gain_db:.2f} dB'.encode()
                ]
            if values.album_peak is not None:
                audio['----:com.apple.iTunes:REPLAYGAIN_ALBUM_PEAK'] = [
                    f'{values.album_peak:.6f}'.encode()
                ]
            audio.save()
        elif suffix in {'.opus', '.ogg'}:
            from mutagen.oggopus import OggOpus

            try:
                audio = OggOpus(path)
            except Exception:
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
            width, height = _image_size(image_bytes)
            if width and height:
                pic.width = width
                pic.height = height
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
        elif suffix in {'.m4a', '.mp4', '.aac'}:
            from mutagen.mp4 import MP4, MP4Cover

            audio = MP4(path)
            fmt = (
                MP4Cover.FORMAT_PNG
                if mime == 'image/png'
                else MP4Cover.FORMAT_JPEG
            )
            audio['covr'] = [MP4Cover(image_bytes, imageformat=fmt)]
            audio.save()
        elif suffix in {'.opus', '.ogg'}:
            import base64

            from mutagen.flac import Picture
            from mutagen.oggopus import OggOpus

            try:
                audio = OggOpus(path)
            except Exception:
                from mutagen.oggvorbis import OggVorbis

                audio = OggVorbis(path)
            pic = Picture()
            pic.type = 3
            pic.mime = mime
            pic.desc = 'Cover'
            pic.data = image_bytes
            width, height = _image_size(image_bytes)
            if width and height:
                pic.width = width
                pic.height = height
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
        except Exception:
            from mutagen.oggvorbis import OggVorbis

            audio = OggVorbis(path)
        self._apply_vorbis(audio, **kw)
        audio.save()

    def _apply_vorbis(
        self,
        audio,
        *,
        title: str,
        artists: list[str],
        album: str,
        album_artists: list[str],
        date: str,
        track_number: int,
        total_tracks: int | None,
        album_meta: AlbumMetadata | None = None,
        track_meta: TrackMetadata | None = None,
    ) -> None:
        audio['TITLE'] = [title]
        audio['ARTIST'] = artists
        audio['ALBUM'] = [album]
        audio['ALBUMARTIST'] = album_artists
        if date:
            audio['DATE'] = [date]
            audio['ORIGINALDATE'] = [date]
        audio['TRACKNUMBER'] = [str(track_number)]
        if total_tracks and total_tracks > 0:
            audio['TRACKTOTAL'] = [str(total_tracks)]
        disc_num, disc_total = _disc_numbers(album_meta)
        if disc_num is not None:
            audio['DISCNUMBER'] = [str(disc_num)]
            if disc_total and disc_total > 0:
                audio['DISCTOTAL'] = [str(disc_total)]
        audio['MEDIA'] = ['CD']
        if _is_compilation(album_meta):
            audio['COMPILATION'] = ['1']
        if album_meta and album_meta.musicbrainz_release_id:
            audio['MUSICBRAINZ_ALBUMID'] = [album_meta.musicbrainz_release_id]
        if album_meta and album_meta.musicbrainz_release_group_id:
            audio['MUSICBRAINZ_RELEASEGROUPID'] = [
                album_meta.musicbrainz_release_group_id
            ]
        if track_meta is not None and track_meta.musicbrainz_recording_id:
            audio['MUSICBRAINZ_RECORDINGID'] = [track_meta.musicbrainz_recording_id]
        if album_meta and album_meta.barcode:
            audio['BARCODE'] = [album_meta.barcode]
        if album_meta and album_meta.genre:
            audio['GENRE'] = [album_meta.genre]
        if album_meta and album_meta.label:
            audio['LABEL'] = [album_meta.label]
        if album_meta and album_meta.catalog_number:
            audio['CATALOGNUMBER'] = [album_meta.catalog_number]
        if album_meta and album_meta.country:
            audio['RELEASECOUNTRY'] = [album_meta.country]
        if album_meta and album_meta.discid:
            audio['MUSICBRAINZ_DISCID'] = [album_meta.discid]

    def _tag_mp4(
        self,
        path: Path,
        *,
        title: str,
        artists: list[str],
        album: str,
        album_artists: list[str],
        date: str,
        track_number: int,
        total_tracks: int | None,
        album_meta: AlbumMetadata | None = None,
        track_meta: TrackMetadata | None = None,
    ) -> None:
        from mutagen.mp4 import MP4

        audio = MP4(path)
        audio['\xa9nam'] = [title]
        audio['\xa9ART'] = artists
        audio['\xa9alb'] = [album]
        audio['aART'] = album_artists
        total = total_tracks if total_tracks and total_tracks > 0 else 0
        audio['trkn'] = [(track_number, total)]
        disc_num, disc_total = _disc_numbers(album_meta)
        if disc_num is not None:
            audio['disk'] = [(disc_num, disc_total if disc_total else 0)]
        if date:
            audio['\xa9day'] = [date]
        if album_meta and album_meta.genre:
            audio['\xa9gen'] = [album_meta.genre]
        if album_meta and album_meta.label:
            audio['\xa9pub'] = [album_meta.label]
        if _is_compilation(album_meta):
            audio['cpil'] = [True]
        if album_meta and album_meta.musicbrainz_release_id:
            audio['----:com.apple.iTunes:MusicBrainz Album Id'] = [
                album_meta.musicbrainz_release_id.encode('utf-8')
            ]
        if album_meta and album_meta.musicbrainz_release_group_id:
            audio['----:com.apple.iTunes:MusicBrainz Release Group Id'] = [
                album_meta.musicbrainz_release_group_id.encode('utf-8')
            ]
        # Picard names this "Track Id" but the value is the recording UUID.
        if track_meta is not None and track_meta.musicbrainz_recording_id:
            audio['----:com.apple.iTunes:MusicBrainz Track Id'] = [
                track_meta.musicbrainz_recording_id.encode('utf-8')
            ]
        if album_meta and album_meta.barcode:
            audio['----:com.apple.iTunes:BARCODE'] = [
                album_meta.barcode.encode('utf-8')
            ]
        if album_meta and album_meta.catalog_number:
            audio['----:com.apple.iTunes:CATALOGNUMBER'] = [
                album_meta.catalog_number.encode('utf-8')
            ]
        if album_meta and album_meta.country:
            audio['----:com.apple.iTunes:MusicBrainz Album Release Country'] = [
                album_meta.country.encode('utf-8')
            ]
        if album_meta and album_meta.discid:
            audio['----:com.apple.iTunes:MusicBrainz Disc Id'] = [
                album_meta.discid.encode('utf-8')
            ]
        audio['----:com.apple.iTunes:MEDIA'] = [b'CD']
        audio.save()

    def _tag_mp3(
        self,
        path: Path,
        *,
        title: str,
        artists: list[str],
        album: str,
        album_artists: list[str],
        date: str,
        track_number: int,
        total_tracks: int | None,
        album_meta: AlbumMetadata | None = None,
        track_meta: TrackMetadata | None = None,
    ) -> None:
        from mutagen.id3 import (
            ID3,
            TALB,
            TCON,
            TDRC,
            TIT2,
            TPE1,
            TPE2,
            TPOS,
            TPUB,
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
        for frame in (
            'TIT2',
            'TPE1',
            'TALB',
            'TPE2',
            'TRCK',
            'TPOS',
            'TDRC',
            'TDOR',
            'TCON',
            'TPUB',
            'TCMP',
            'TMED',
        ):
            tags.delall(frame)
        tags.add(TIT2(encoding=3, text=title))
        tags.add(TPE1(encoding=3, text=artists))
        tags.add(TALB(encoding=3, text=album))
        tags.add(TPE2(encoding=3, text=album_artists))
        if total_tracks and total_tracks > 0:
            tags.add(TRCK(encoding=3, text=f'{track_number}/{total_tracks}'))
        else:
            tags.add(TRCK(encoding=3, text=str(track_number)))
        disc_num, disc_total = _disc_numbers(album_meta)
        if disc_num is not None:
            total = disc_total if disc_total and disc_total > 0 else 1
            tags.add(TPOS(encoding=3, text=f'{disc_num}/{total}'))
        if date:
            tags.add(TDRC(encoding=3, text=date))
            try:
                from mutagen.id3 import TDOR

                tags.add(TDOR(encoding=3, text=date))
            except ImportError:
                pass
        if album_meta and album_meta.genre:
            tags.add(TCON(encoding=3, text=album_meta.genre))
        if album_meta and album_meta.label:
            tags.add(TPUB(encoding=3, text=album_meta.label))
        try:
            from mutagen.id3 import TMED

            tags.add(TMED(encoding=3, text='CD'))
        except ImportError:
            tags.delall('TXXX:MEDIA')
            tags.add(TXXX(encoding=3, desc='MEDIA', text=['CD']))
        if _is_compilation(album_meta):
            try:
                from mutagen.id3 import TCMP

                tags.add(TCMP(encoding=3, text='1'))
            except ImportError:
                tags.delall('TXXX:COMPILATION')
                tags.add(TXXX(encoding=3, desc='COMPILATION', text=['1']))
        if album_meta and album_meta.musicbrainz_release_id:
            tags.delall('TXXX:MusicBrainz Album Id')
            tags.add(
                TXXX(
                    encoding=3,
                    desc='MusicBrainz Album Id',
                    text=[album_meta.musicbrainz_release_id],
                )
            )
        if album_meta and album_meta.musicbrainz_release_group_id:
            tags.delall('TXXX:MusicBrainz Release Group Id')
            tags.add(
                TXXX(
                    encoding=3,
                    desc='MusicBrainz Release Group Id',
                    text=[album_meta.musicbrainz_release_group_id],
                )
            )
        if track_meta is not None and track_meta.musicbrainz_recording_id:
            tags.delall('UFID:http://musicbrainz.org')
            tags.add(
                UFID(
                    owner='http://musicbrainz.org',
                    data=track_meta.musicbrainz_recording_id.encode('ascii'),
                )
            )
        if album_meta and album_meta.barcode:
            tags.delall('TXXX:BARCODE')
            tags.add(TXXX(encoding=3, desc='BARCODE', text=[album_meta.barcode]))
        if album_meta and album_meta.catalog_number:
            tags.delall('TXXX:CATALOGNUMBER')
            tags.add(
                TXXX(
                    encoding=3,
                    desc='CATALOGNUMBER',
                    text=[album_meta.catalog_number],
                )
            )
        if album_meta and album_meta.country:
            tags.delall('TXXX:MusicBrainz Album Release Country')
            tags.add(
                TXXX(
                    encoding=3,
                    desc='MusicBrainz Album Release Country',
                    text=[album_meta.country],
                )
            )
        if album_meta and album_meta.discid:
            tags.delall('TXXX:MusicBrainz Disc Id')
            tags.add(
                TXXX(
                    encoding=3,
                    desc='MusicBrainz Disc Id',
                    text=[album_meta.discid],
                )
            )
        # Drop legacy TXXX:LABEL if present (publisher is TPUB now).
        tags.delall('TXXX:LABEL')
        # ID3v2.4 keeps full ISO dates in TDRC/TDOR (v2.3 TYER is year-only).
        audio.save(v2_version=4)

    def _tag_wav(
        self,
        path: Path,
        *,
        title: str,
        artists: list[str],
        album: str,
        album_artists: list[str],
        date: str,
        track_number: int,
        total_tracks: int | None,
        album_meta: AlbumMetadata | None = None,
        track_meta: TrackMetadata | None = None,
    ) -> None:
        del track_meta  # WAV keeps a minimal ID3 subset.
        try:
            from mutagen.wave import WAVE
        except ImportError:
            log.debug('mutagen WAVE not available')
            return
        audio = WAVE(path)
        if audio.tags is None:
            audio.add_tags()
        tags = audio.tags
        try:
            from mutagen.id3 import ID3, TALB, TDRC, TIT2, TPE1, TPE2, TPOS, TRCK

            if tags is None or not isinstance(tags, ID3):
                return
            tags.add(TIT2(encoding=3, text=title))
            tags.add(TPE1(encoding=3, text=artists))
            tags.add(TALB(encoding=3, text=album))
            tags.add(TPE2(encoding=3, text=album_artists))
            if total_tracks and total_tracks > 0:
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
        except Exception:
            log.debug('WAV tagging failed for %s', path, exc_info=True)


def _artist_names_for_tags(value: str, *, style: str) -> list[str]:
    """Artist names for native multi-value tags (feat. → primary only)."""
    if normalize_artist_style(style) == ARTIST_STYLE_FEAT:
        primary = primary_artist(value)
        return [primary] if primary else ['Unknown Artist']
    names = split_artists(value)
    return names or ['Unknown Artist']


def _is_compilation(album_meta: AlbumMetadata | None) -> bool:
    if album_meta is None:
        return False
    name = (album_meta.artist or '').strip().casefold()
    return name in {'various artists', 'various', 'va', 'v/a'}


def _image_size(image_bytes: bytes) -> tuple[int, int]:
    try:
        from io import BytesIO

        from PIL import Image

        with Image.open(BytesIO(image_bytes)) as img:
            return int(img.width), int(img.height)
    except Exception:
        return 0, 0


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
