# SPDX-License-Identifier: GPL-3.0-or-later
"""Write tags, ReplayGain, and embedded pictures via mutagen."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from ready2rip.config import APPLICATION_NAME, APPLICATION_VERSION
from ready2rip.metadata.lyrics import embed_lyrics_text
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


def ripper_tag_value() -> str:
    """Encoded-by string: ``ready2rip <version>``."""
    return f'{APPLICATION_NAME} {APPLICATION_VERSION}'


@dataclass
class ReplayGainValues:
    track_gain_db: float
    track_peak: float
    album_gain_db: float | None = None
    album_peak: float | None = None


class TagWriter:
    """Write the trimmed tag set, ReplayGain, and embedded pictures."""

    def write_album_tags(
        self,
        path: Path,
        album: AlbumMetadata,
        track_number: int,
        *,
        total_tracks: int | None = None,
        artist_style: str | None = DEFAULT_ARTIST_STYLE,
        image: bool = False,
        encoded_by: str | None = None,
        embed_lyrics: bool = True,
    ) -> None:
        """Tag an encoded file.

        When *image* is True, write album-level identity only (no track/ISRC).
        """
        suffix = path.suffix.lower()
        style = normalize_artist_style(artist_style)
        album_title = album.title or 'Unknown Album'
        raw_album_artist = normalize_artists(album.artist or 'Unknown Artist')
        album_artists = _people_tag_values(
            _artist_names_for_tags(raw_album_artist, style=style)
        )

        lyrics = ''
        synced_lyrics = ''
        if image:
            title = album_title
            artists = album_artists
            track_number = 0
            total = None
        else:
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
            if style == ARTIST_STYLE_FEAT:
                title = format_title_with_artists(raw_title, raw_artist, style)
            else:
                title = raw_title
            artists = _people_tag_values(
                _artist_names_for_tags(raw_artist, style=style)
            )
            total = total_tracks if total_tracks is not None else (
                len(album.tracks) if album.tracks else None
            )
            if embed_lyrics:
                lyrics = embed_lyrics_text(track)
                synced_lyrics = (
                    (track.lyrics_synced or '').strip() if track is not None else ''
                )
        kw = dict(
            title=title,
            artists=artists,
            album=album_title,
            album_artists=album_artists,
            track_number=track_number,
            total_tracks=total,
            album_meta=album,
            encoded_by=(encoded_by or '').strip() or None,
            lyrics=lyrics,
            synced_lyrics=synced_lyrics,
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
        elif suffix in {'.aiff', '.aif'}:
            self._tag_aiff(path, **kw)
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
            audio.save(v2_version=4)
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
        elif suffix in {'.wav', '.aiff', '.aif'}:
            self._write_id3_replaygain(path, values, container=suffix)
        else:
            log.debug('No ReplayGain writer for %s', path)

    def write_sound_check(
        self,
        path: Path,
        *,
        gain_db: float,
        peak: float,
    ) -> bool:
        """Write iTunNORM for iPod-compatible formats. Return True if written."""
        from ready2rip.rip.soundcheck import (
            replaygain_to_itunnorm,
            supports_sound_check,
        )

        suffix = path.suffix.lower()
        if not supports_sound_check(suffix):
            return False
        text = replaygain_to_itunnorm(gain_db, peak)
        try:
            if suffix == '.mp3':
                self._write_mp3_itunnorm(path, text)
            elif suffix in {'.m4a', '.mp4', '.aac'}:
                self._write_mp4_itunnorm(path, text)
            elif suffix in {'.wav', '.aiff', '.aif'}:
                self._write_id3_itunnorm(path, text, container=suffix)
            else:
                return False
        except Exception:
            log.debug('Sound Check write failed for %s', path, exc_info=True)
            return False
        return True

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
        elif suffix in {'.aiff', '.aif', '.wav'}:
            self._embed_id3_artwork(path, image_bytes, mime, container=suffix)
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

    @staticmethod
    def _open_id3_container(path: Path, *, container: str):
        """Open WAVE or AIFF with an ID3 tag bag."""
        from mutagen.id3 import ID3

        if container == '.wav':
            from mutagen.wave import WAVE

            audio = WAVE(path)
        else:
            from mutagen.aiff import AIFF

            audio = AIFF(path)
        if audio.tags is None:
            audio.add_tags()
        if audio.tags is None or not isinstance(audio.tags, ID3):
            raise RuntimeError(f'No ID3 tags on {path}')
        return audio

    def _write_id3_replaygain(
        self,
        path: Path,
        values: ReplayGainValues,
        *,
        container: str,
    ) -> None:
        from mutagen.id3 import TXXX

        audio = self._open_id3_container(path, container=container)
        tags = audio.tags
        assert tags is not None
        for desc, text in (
            ('REPLAYGAIN_TRACK_GAIN', f'{values.track_gain_db:.2f} dB'),
            ('REPLAYGAIN_TRACK_PEAK', f'{values.track_peak:.6f}'),
        ):
            tags.delall(f'TXXX:{desc}')
            tags.add(TXXX(encoding=3, desc=desc, text=[text]))
        if values.album_gain_db is not None:
            tags.delall('TXXX:REPLAYGAIN_ALBUM_GAIN')
            tags.add(
                TXXX(
                    encoding=3,
                    desc='REPLAYGAIN_ALBUM_GAIN',
                    text=[f'{values.album_gain_db:.2f} dB'],
                )
            )
        if values.album_peak is not None:
            tags.delall('TXXX:REPLAYGAIN_ALBUM_PEAK')
            tags.add(
                TXXX(
                    encoding=3,
                    desc='REPLAYGAIN_ALBUM_PEAK',
                    text=[f'{values.album_peak:.6f}'],
                )
            )
        audio.save()

    def _write_mp3_itunnorm(self, path: Path, text: str) -> None:
        from mutagen.id3 import COMM, ID3
        from mutagen.mp3 import MP3

        audio = MP3(path, ID3=ID3)
        if audio.tags is None:
            audio.add_tags()
        assert audio.tags is not None
        audio.tags.delall('COMM:iTunNORM:eng')
        audio.tags.delall('COMM:iTunNORM:')
        # Match iTunes: description iTunNORM, empty language often used too.
        audio.tags.add(
            COMM(encoding=3, lang='eng', desc='iTunNORM', text=[text])
        )
        audio.save(v2_version=4)

    def _write_mp4_itunnorm(self, path: Path, text: str) -> None:
        from mutagen.mp4 import MP4, MP4FreeForm

        audio = MP4(path)
        audio['----:com.apple.iTunes:iTunNORM'] = [
            MP4FreeForm(text.encode('utf-8'))
        ]
        audio.save()

    def _write_id3_itunnorm(
        self, path: Path, text: str, *, container: str
    ) -> None:
        from mutagen.id3 import COMM

        audio = self._open_id3_container(path, container=container)
        tags = audio.tags
        assert tags is not None
        tags.delall('COMM:iTunNORM:eng')
        tags.delall('COMM:iTunNORM:')
        tags.add(COMM(encoding=3, lang='eng', desc='iTunNORM', text=[text]))
        audio.save()

    def _embed_id3_artwork(
        self,
        path: Path,
        image_bytes: bytes,
        mime: str,
        *,
        container: str,
    ) -> None:
        from mutagen.id3 import APIC

        audio = self._open_id3_container(path, container=container)
        tags = audio.tags
        assert tags is not None
        tags.delall('APIC')
        tags.add(
            APIC(
                encoding=3,
                mime=mime or 'image/jpeg',
                type=3,
                desc='Cover',
                data=image_bytes,
            )
        )
        audio.save()

    def _tag_aiff(self, path: Path, **kw) -> None:
        """AIFF ID3 tags — same fields as WAV."""
        self._tag_id3_pcm_container(path, container='.aiff', **kw)

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
        track_number: int,
        total_tracks: int | None,
        album_meta: AlbumMetadata | None = None,
        encoded_by: str | None = None,
        lyrics: str = '',
        synced_lyrics: str = '',
    ) -> None:
        audio['TITLE'] = [title]
        # Vorbis comments: one ARTIST=/ALBUMARTIST= field per name.
        audio['ARTIST'] = _people_tag_values(artists)
        audio['ALBUM'] = [album]
        audio['ALBUMARTIST'] = _people_tag_values(album_artists)
        if track_number > 0:
            audio['TRACKNUMBER'] = [str(track_number)]
            if total_tracks and total_tracks > 0:
                audio['TRACKTOTAL'] = [str(total_tracks)]
        else:
            audio.pop('TRACKNUMBER', None)
            audio.pop('TRACKTOTAL', None)
        disc_num, disc_total = _disc_numbers(album_meta)
        if disc_num is not None:
            audio['DISCNUMBER'] = [str(disc_num)]
            if disc_total and disc_total > 0:
                audio['DISCTOTAL'] = [str(disc_total)]
        if album_meta and album_meta.genre:
            audio['GENRE'] = [album_meta.genre]
        if album_meta and album_meta.label:
            audio['LABEL'] = [album_meta.label]
        date = _album_date(album_meta)
        if date:
            audio['DATE'] = [date]
        barcode = _album_barcode(album_meta)
        if barcode:
            audio['BARCODE'] = [barcode]
        if track_number > 0:
            isrc = _track_isrc(album_meta, track_number)
            if isrc:
                audio['ISRC'] = [isrc]
            else:
                audio.pop('ISRC', None)
        else:
            audio.pop('ISRC', None)
        if _is_compilation(album_meta):
            audio['COMPILATION'] = ['1']
        if encoded_by:
            audio['ENCODER'] = [encoded_by]
        else:
            audio.pop('ENCODER', None)
        _apply_vorbis_lyrics(audio, lyrics=lyrics, synced_lyrics=synced_lyrics)

    def _tag_mp4(
        self,
        path: Path,
        *,
        title: str,
        artists: list[str],
        album: str,
        album_artists: list[str],
        track_number: int,
        total_tracks: int | None,
        album_meta: AlbumMetadata | None = None,
        encoded_by: str | None = None,
        lyrics: str = '',
        synced_lyrics: str = '',
    ) -> None:
        from mutagen.mp4 import MP4, MP4FreeForm

        audio = MP4(path)
        people = _people_tag_values(artists)
        album_people = _people_tag_values(album_artists)
        audio['\xa9nam'] = [title]
        # Native MP4 multi-value artist atoms (one string per artist).
        audio['\xa9ART'] = people
        audio['\xa9alb'] = [album]
        audio['aART'] = album_people
        if track_number > 0:
            total = total_tracks if total_tracks and total_tracks > 0 else 0
            audio['trkn'] = [(track_number, total)]
        elif 'trkn' in audio:
            del audio['trkn']
        disc_num, disc_total = _disc_numbers(album_meta)
        if disc_num is not None:
            audio['disk'] = [(disc_num, disc_total if disc_total else 0)]
        if album_meta and album_meta.genre:
            audio['\xa9gen'] = [album_meta.genre]
        if album_meta and album_meta.label:
            audio['\xa9pub'] = [album_meta.label]
        date = _album_date(album_meta)
        if date:
            audio['\xa9day'] = [date]
        barcode = _album_barcode(album_meta)
        if barcode:
            audio['----:com.apple.iTunes:BARCODE'] = [
                MP4FreeForm(barcode.encode('utf-8'))
            ]
        isrc_key = '----:com.apple.iTunes:ISRC'
        if track_number > 0:
            isrc = _track_isrc(album_meta, track_number)
            if isrc:
                audio[isrc_key] = [MP4FreeForm(isrc.encode('utf-8'))]
            elif isrc_key in audio:
                del audio[isrc_key]
        elif isrc_key in audio:
            del audio[isrc_key]
        if _is_compilation(album_meta):
            audio['cpil'] = [True]
        if encoded_by:
            audio['\xa9too'] = [encoded_by]
        elif '\xa9too' in audio:
            del audio['\xa9too']
        text = (lyrics or '').strip() or (synced_lyrics or '').strip()
        if text:
            audio['\xa9lyr'] = [text]
        elif '\xa9lyr' in audio:
            del audio['\xa9lyr']
        audio.save()

    def _tag_mp3(
        self,
        path: Path,
        *,
        title: str,
        artists: list[str],
        album: str,
        album_artists: list[str],
        track_number: int,
        total_tracks: int | None,
        album_meta: AlbumMetadata | None = None,
        encoded_by: str | None = None,
        lyrics: str = '',
        synced_lyrics: str = '',
    ) -> None:
        from mutagen.id3 import (
            ID3,
            TALB,
            TCON,
            TDRC,
            TENC,
            TIT2,
            TPE1,
            TPE2,
            TPOS,
            TPUB,
            TRCK,
            TSRC,
            TXXX,
            USLT,
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
            'TCON',
            'TPUB',
            'TCMP',
            'TDRC',
            'TSRC',
            'TENC',
        ):
            tags.delall(frame)
        tags.delall('TXXX:BARCODE')
        people = _people_tag_values(artists)
        album_people = _people_tag_values(album_artists)
        tags.add(TIT2(encoding=3, text=title))
        # ID3v2.4 null-separated multi-value (one name per value).
        tags.add(TPE1(encoding=3, text=people))
        tags.add(TALB(encoding=3, text=album))
        tags.add(TPE2(encoding=3, text=album_people))
        if track_number > 0:
            if total_tracks and total_tracks > 0:
                tags.add(TRCK(encoding=3, text=f'{track_number}/{total_tracks}'))
            else:
                tags.add(TRCK(encoding=3, text=str(track_number)))
        disc_num, disc_total = _disc_numbers(album_meta)
        if disc_num is not None:
            total = disc_total if disc_total and disc_total > 0 else 1
            tags.add(TPOS(encoding=3, text=f'{disc_num}/{total}'))
        if album_meta and album_meta.genre:
            tags.add(TCON(encoding=3, text=album_meta.genre))
        if album_meta and album_meta.label:
            tags.add(TPUB(encoding=3, text=album_meta.label))
        date = _album_date(album_meta)
        if date:
            tags.add(TDRC(encoding=3, text=date))
        barcode = _album_barcode(album_meta)
        if barcode:
            tags.add(TXXX(encoding=3, desc='BARCODE', text=[barcode]))
        if track_number > 0:
            isrc = _track_isrc(album_meta, track_number)
            if isrc:
                tags.add(TSRC(encoding=3, text=isrc))
        if _is_compilation(album_meta):
            try:
                from mutagen.id3 import TCMP

                tags.add(TCMP(encoding=3, text='1'))
            except ImportError:
                tags.delall('TXXX:COMPILATION')
                tags.add(TXXX(encoding=3, desc='COMPILATION', text=['1']))
        tags.delall('TXXX:LABEL')
        if encoded_by:
            tags.add(TENC(encoding=3, text=encoded_by))
        tags.delall('USLT')
        uslt_text = (lyrics or '').strip() or (synced_lyrics or '').strip()
        if uslt_text:
            tags.add(USLT(encoding=3, lang='eng', desc='', text=uslt_text))
        audio.save(v2_version=4)

    def _tag_wav(self, path: Path, **kw) -> None:
        self._tag_id3_pcm_container(path, container='.wav', **kw)

    def _tag_id3_pcm_container(
        self,
        path: Path,
        *,
        container: str,
        title: str,
        artists: list[str],
        album: str,
        album_artists: list[str],
        track_number: int,
        total_tracks: int | None,
        album_meta: AlbumMetadata | None = None,
        encoded_by: str | None = None,
        lyrics: str = '',
        synced_lyrics: str = '',
    ) -> None:
        """Shared ID3 tagging for WAV and AIFF."""
        try:
            from mutagen.id3 import (
                TALB,
                TCON,
                TDRC,
                TENC,
                TIT2,
                TPE1,
                TPE2,
                TPOS,
                TPUB,
                TRCK,
                TSRC,
                TXXX,
                USLT,
            )

            audio = self._open_id3_container(path, container=container)
            tags = audio.tags
            assert tags is not None
            for frame in (
                'TIT2',
                'TPE1',
                'TALB',
                'TPE2',
                'TRCK',
                'TPOS',
                'TCON',
                'TPUB',
                'TDRC',
                'TSRC',
                'TENC',
            ):
                tags.delall(frame)
            tags.delall('TXXX:BARCODE')
            people = _people_tag_values(artists)
            album_people = _people_tag_values(album_artists)
            tags.add(TIT2(encoding=3, text=title))
            tags.add(TPE1(encoding=3, text=people))
            tags.add(TALB(encoding=3, text=album))
            tags.add(TPE2(encoding=3, text=album_people))
            if track_number > 0:
                if total_tracks and total_tracks > 0:
                    tags.add(
                        TRCK(encoding=3, text=f'{track_number}/{total_tracks}')
                    )
                else:
                    tags.add(TRCK(encoding=3, text=str(track_number)))
            disc_num, disc_total = _disc_numbers(album_meta)
            if disc_num is not None:
                total = disc_total if disc_total and disc_total > 0 else 1
                tags.add(TPOS(encoding=3, text=f'{disc_num}/{total}'))
            if album_meta and album_meta.genre:
                tags.add(TCON(encoding=3, text=album_meta.genre))
            if album_meta and album_meta.label:
                tags.add(TPUB(encoding=3, text=album_meta.label))
            date = _album_date(album_meta)
            if date:
                tags.add(TDRC(encoding=3, text=date))
            barcode = _album_barcode(album_meta)
            if barcode:
                tags.add(TXXX(encoding=3, desc='BARCODE', text=[barcode]))
            if track_number > 0:
                isrc = _track_isrc(album_meta, track_number)
                if isrc:
                    tags.add(TSRC(encoding=3, text=isrc))
            if encoded_by:
                tags.add(TENC(encoding=3, text=encoded_by))
            tags.delall('USLT')
            uslt_text = (lyrics or '').strip() or (synced_lyrics or '').strip()
            if uslt_text:
                tags.add(USLT(encoding=3, lang='eng', desc='', text=uslt_text))
            # ID3v2.4 keeps multi-artist TPE1/TPE2 as separate values.
            audio.save(v2_version=4)
        except Exception:
            log.debug(
                '%s tagging failed for %s',
                container.lstrip('.').upper(),
                path,
                exc_info=True,
            )


def _apply_vorbis_lyrics(audio, *, lyrics: str, synced_lyrics: str) -> None:
    plain = (lyrics or '').strip()
    synced = (synced_lyrics or '').strip()
    if plain:
        audio['LYRICS'] = [plain]
        audio['UNSYNCEDLYRICS'] = [plain]
    else:
        audio.pop('LYRICS', None)
        audio.pop('UNSYNCEDLYRICS', None)
    if synced:
        audio['SYNCEDLYRICS'] = [synced]
    else:
        audio.pop('SYNCEDLYRICS', None)


def _people_tag_values(names: list[str] | str | None) -> list[str]:
    """Flatten to one artist name per list entry (never ``A; B`` in one string)."""
    if names is None:
        return ['Unknown Artist']
    if isinstance(names, str):
        return split_artists(names) or ['Unknown Artist']
    out: list[str] = []
    for name in names:
        parts = split_artists(name)
        if parts:
            out.extend(parts)
        else:
            text = str(name).strip()
            if text:
                out.append(text)
    return out or ['Unknown Artist']


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


def _album_date(album_meta: AlbumMetadata | None) -> str:
    if album_meta is None:
        return ''
    return normalize_release_date((album_meta.date or '').strip())


def _album_barcode(album_meta: AlbumMetadata | None) -> str:
    if album_meta is None:
        return ''
    digits = ''.join(ch for ch in (album_meta.barcode or '') if ch.isdigit())
    return digits if len(digits) >= 12 else ''


def _track_isrc(album_meta: AlbumMetadata | None, track_number: int) -> str:
    if album_meta is None:
        return ''
    meta: TrackMetadata | None = track_meta_for(album_meta, track_number)
    raw = (meta.isrc if meta is not None else '') or ''
    cleaned = ''.join(ch for ch in raw.upper() if ch.isalnum())
    return cleaned if len(cleaned) == 12 else ''
