# SPDX-License-Identifier: GPL-3.0-or-later
"""Album artwork download, quality ranking, and resize for embed."""

from __future__ import annotations

import io
import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ready2rip.metadata.providers import AlbumMetadata, USER_AGENT
from ready2rip.util import is_safe_http_url, read_limited

log = logging.getLogger(__name__)

CAA_RELEASE = 'https://coverartarchive.org/release/{mbid}'
ITUNES_SEARCH = 'https://itunes.apple.com/search'
DEEZER_SEARCH_ALBUM = 'https://api.deezer.com/search/album'


@dataclass
class ArtworkImage:
    """In-memory cover image (full download quality unless resized)."""

    data: bytes
    mime: str
    width: int
    height: int
    source: str
    url: str = ''

    @property
    def max_edge(self) -> int:
        return max(self.width, self.height)

    @property
    def label(self) -> str:
        return f'{self.width}×{self.height} · {self.source}'


@dataclass
class ArtworkSourceOptions:
    """Which online cover sources may be queried."""

    itunes: bool = True
    cover_art_archive: bool = True
    deezer: bool = True

    @property
    def any_enabled(self) -> bool:
        return self.itunes or self.cover_art_archive or self.deezer


class ArtworkFetcher:
    """Fetch cover art from enabled sources and pick the highest quality.

    Candidates (when enabled):
    1. Apple Music / iTunes (high-res URL rewrite)
    2. Cover Art Archive original (MusicBrainz release)
    3. Deezer album search (cover_xl / upscaled CDN)
    4. Any direct cover_url already on the album record
    """

    def __init__(self, user_agent: str = USER_AGENT, timeout: float = 30.0) -> None:
        self.user_agent = user_agent
        self.timeout = timeout

    def fetch_best(
        self,
        album: AlbumMetadata,
        sources: ArtworkSourceOptions | None = None,
    ) -> ArtworkImage | None:
        """Query enabled sources and return the largest usable cover."""
        opts = sources or ArtworkSourceOptions()
        candidates: list[ArtworkImage] = []

        if opts.itunes:
            itunes = self.fetch_from_itunes(album.artist, album.title)
            if itunes is not None:
                candidates.append(itunes)

        if opts.cover_art_archive and album.musicbrainz_release_id:
            caa = self.fetch_from_cover_art_archive(album.musicbrainz_release_id)
            if caa is not None:
                candidates.append(caa)

        if opts.deezer:
            deezer = self.fetch_from_deezer(album.artist, album.title)
            if deezer is not None:
                candidates.append(deezer)

        if album.cover_url and _looks_like_image_url(album.cover_url):
            direct = self._download_image(album.cover_url, source='direct')
            if direct is not None:
                candidates.append(direct)

        if not candidates:
            return None

        best = max(
            candidates,
            key=lambda img: (img.max_edge, img.width * img.height, len(img.data)),
        )
        if len(candidates) > 1:
            summary = ', '.join(c.label for c in candidates)
            log.info(
                'Selected highest-quality cover %s from %d candidate(s): %s',
                best.label,
                len(candidates),
                summary,
            )
        else:
            log.info('Selected cover %s', best.label)
        return best

    def load_from_file(self, path: str | Path) -> ArtworkImage | None:
        """Load cover art from a local image file."""
        from ready2rip.util import MAX_DOWNLOAD_BYTES

        file_path = Path(path)
        try:
            size = file_path.stat().st_size
            if size > MAX_DOWNLOAD_BYTES:
                log.warning('Artwork file too large (%s bytes): %s', size, file_path)
                return None
            data = file_path.read_bytes()
        except OSError as exc:
            log.warning('Could not read artwork file %s: %s', file_path, exc)
            return None
        if not data or len(data) < 32:
            return None
        mime = _guess_mime(data, '')
        if mime == 'application/octet-stream':
            suffix = file_path.suffix.casefold()
            mime = {
                '.jpg': 'image/jpeg',
                '.jpeg': 'image/jpeg',
                '.png': 'image/png',
                '.webp': 'image/webp',
                '.gif': 'image/gif',
            }.get(suffix, mime)
        width, height = _image_size(data)
        if width <= 0 or height <= 0:
            return None
        return ArtworkImage(
            data=data,
            mime=mime,
            width=width,
            height=height,
            source='local',
            url=str(file_path),
        )

    def fetch_from_cover_art_archive(self, release_id: str) -> ArtworkImage | None:
        """Download the original front cover from Cover Art Archive."""
        if not release_id:
            return None

        meta_url = CAA_RELEASE.format(mbid=release_id)
        data = self._get_json(meta_url)
        if not data:
            return None

        images = data.get('images') or []
        front_images = [img for img in images if img.get('front')]
        pool = front_images or images
        if not pool:
            return None

        # Prefer approved front, then largest listed thumbnail as a hint,
        # but always download the full ``image`` URL (original).
        def rank(img: dict[str, Any]) -> tuple:
            thumbs = img.get('thumbnails') or {}
            # Parse largest numeric thumbnail key if present
            sizes = []
            for key in thumbs:
                if str(key).isdigit():
                    sizes.append(int(key))
            max_thumb = max(sizes) if sizes else 0
            return (
                1 if img.get('front') else 0,
                1 if img.get('approved', True) else 0,
                max_thumb,
            )

        pool_sorted = sorted(pool, key=rank, reverse=True)
        image_url = pool_sorted[0].get('image')
        if not image_url:
            # Fall back to largest thumbnail URL
            thumbs = pool_sorted[0].get('thumbnails') or {}
            for key in ('1200', 'large', '500', 'small'):
                if key in thumbs:
                    image_url = thumbs[key]
                    break
            if not image_url and thumbs:
                image_url = next(iter(thumbs.values()))
        if not image_url:
            return None

        return self._download_image(image_url, source='coverartarchive')

    def fetch_from_itunes(self, artist: str, album: str) -> ArtworkImage | None:
        """Search iTunes / Apple Music catalog and download high-res artwork."""
        term = ' '.join(p for p in (artist, album) if p).strip()
        if not term:
            return None

        params = {
            'term': term,
            'entity': 'album',
            'limit': '8',
            'media': 'music',
        }
        url = f'{ITUNES_SEARCH}?{urllib.parse.urlencode(params)}'
        payload = self._get_json(url)
        if not payload:
            return None

        results = payload.get('results') or []
        if not results:
            return None

        match = _best_itunes_result(results, artist, album)
        if match is None:
            return None

        art_url = match.get('artworkUrl100') or match.get('artworkUrl60')
        if not art_url:
            return None

        # Request a very large size; Apple serves the maximum available.
        hires = itunes_hires_url(art_url, size=10000)
        image = self._download_image(hires, source='itunes')
        if image is None and hires != art_url:
            image = self._download_image(art_url, source='itunes')
        return image

    def fetch_from_deezer(self, artist: str, album: str) -> ArtworkImage | None:
        """Search Deezer catalog and download the largest available cover."""
        term_parts = []
        if artist.strip():
            term_parts.append(f'artist:"{artist.strip()}"')
        if album.strip():
            term_parts.append(f'album:"{album.strip()}"')
        if not term_parts:
            return None
        # Fallback plain search if structured query is too strict.
        queries = [
            ' '.join(term_parts),
            ' '.join(p for p in (artist, album) if p).strip(),
        ]

        match: dict[str, Any] | None = None
        for q in queries:
            if not q:
                continue
            params = {'q': q, 'limit': '12'}
            url = f'{DEEZER_SEARCH_ALBUM}?{urllib.parse.urlencode(params)}'
            payload = self._get_json(url)
            if not payload:
                continue
            results = payload.get('data') or []
            if not results:
                continue
            match = _best_deezer_result(results, artist, album)
            if match is not None:
                break

        if match is None:
            return None

        # Prefer cover_xl (typically 1000×1000); try CDN upscales next.
        urls: list[str] = []
        for key in ('cover_xl', 'cover_big', 'cover_medium', 'cover'):
            u = match.get(key)
            if u and isinstance(u, str):
                urls.append(u)
        # Deezer CDN paths often include 1000x1000 — try larger variants.
        expanded: list[str] = []
        for u in urls:
            expanded.append(u)
            for size in (1400, 1200, 1000):
                rewritten = re.sub(
                    r'/\d+x\d+(?:-[^/]*)?(\.(?:jpg|jpeg|png|webp))',
                    f'/{size}x{size}\\1',
                    u,
                    count=1,
                    flags=re.IGNORECASE,
                )
                if rewritten != u and rewritten not in expanded:
                    expanded.append(rewritten)

        for art_url in expanded:
            image = self._download_image(art_url, source='deezer')
            if image is not None:
                return image
        return None

    def resize(self, image: ArtworkImage, max_edge: int) -> ArtworkImage:
        """Scale so the longest edge is at most *max_edge* (0 = original)."""
        if max_edge <= 0 or image.max_edge <= max_edge:
            return image

        try:
            from PIL import Image
        except ImportError:
            return self._resize_gdk(image, max_edge)

        with Image.open(io.BytesIO(image.data)) as im:
            # Flatten alpha so JPEG embeds stay valid.
            if im.mode in ('RGBA', 'LA', 'P'):
                rgba = im.convert('RGBA')
                background = Image.new('RGB', rgba.size, (255, 255, 255))
                background.paste(rgba, mask=rgba.split()[-1])
                im = background
            elif im.mode != 'RGB':
                im = im.convert('RGB')
            # LANCZOS for high-quality downscale
            im.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
            width, height = im.size
            buf = io.BytesIO()
            # JPEG for embeds is widely supported in audio tags.
            im.save(buf, format='JPEG', quality=90, optimize=True)
            mime = 'image/jpeg'
            return ArtworkImage(
                data=buf.getvalue(),
                mime=mime,
                width=width,
                height=height,
                source=f'{image.source}/embed-{max_edge}',
                url=image.url,
            )

    def _resize_gdk(self, image: ArtworkImage, max_edge: int) -> ArtworkImage:
        import gi

        gi.require_version('GdkPixbuf', '2.0')
        from gi.repository import GdkPixbuf, GLib

        loader = GdkPixbuf.PixbufLoader()
        try:
            loader.write(image.data)
            loader.close()
        except GLib.Error:
            return image
        pixbuf = loader.get_pixbuf()
        if pixbuf is None:
            return image

        w, h = pixbuf.get_width(), pixbuf.get_height()
        scale = max_edge / max(w, h)
        nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
        scaled = pixbuf.scale_simple(nw, nh, GdkPixbuf.InterpType.BILINEAR)
        if scaled is None:
            return image
        ok, buf = scaled.save_to_bufferv('jpeg', ['quality'], ['90'])
        if not ok:
            return image
        return ArtworkImage(
            data=bytes(buf),
            mime='image/jpeg',
            width=nw,
            height=nh,
            source=f'{image.source}/embed-{max_edge}',
            url=image.url,
        )

    def _get_json(self, url: str) -> dict[str, Any] | None:
        if not is_safe_http_url(url):
            log.warning('Refusing non-http(s) JSON URL: %s', url)
            return None
        request = urllib.request.Request(
            url,
            headers={
                'User-Agent': self.user_agent,
                'Accept': 'application/json',
            },
            method='GET',
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                # Cover Art Archive may redirect; urlopen follows redirects.
                final = response.geturl()
                if final and not is_safe_http_url(final):
                    log.warning('Refusing JSON redirect to unsafe URL: %s', final)
                    return None
                body = read_limited(response).decode('utf-8', errors='replace')
                return json.loads(body)
        except urllib.error.HTTPError as exc:
            log.debug('HTTP %s for %s', exc.code, url)
            _close_http_error(exc)
            return None
        except (
            urllib.error.URLError,
            TimeoutError,
            json.JSONDecodeError,
            OSError,
            ValueError,
        ) as exc:
            log.warning('JSON fetch failed (%s): %s', url, exc)
            return None

    def _download_image(self, url: str, *, source: str) -> ArtworkImage | None:
        if not is_safe_http_url(url):
            log.warning('Refusing non-http(s) image URL: %s', url)
            return None
        request = urllib.request.Request(
            url,
            headers={
                'User-Agent': self.user_agent,
                'Accept': 'image/avif,image/webp,image/apng,image/*,*/*;q=0.8',
            },
            method='GET',
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                final_url = response.geturl()
                if final_url and not is_safe_http_url(final_url):
                    log.warning('Refusing image redirect to unsafe URL: %s', final_url)
                    return None
                data = read_limited(response)
                content_type = response.headers.get_content_type() or ''
        except urllib.error.HTTPError as exc:
            log.debug('Image HTTP %s for %s', exc.code, url)
            _close_http_error(exc)
            return None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            log.warning('Image download failed (%s): %s', url, exc)
            return None

        if not data or len(data) < 100:
            return None

        mime = _guess_mime(data, content_type)
        width, height = _image_size(data)
        if width <= 0 or height <= 0:
            return None

        return ArtworkImage(
            data=data,
            mime=mime,
            width=width,
            height=height,
            source=source,
            url=final_url or url,
        )


def _close_http_error(exc: urllib.error.HTTPError) -> None:
    """Drain/close HTTPError to avoid ResourceWarning on CPython."""
    try:
        exc.read()
    except Exception:  # noqa: BLE001
        pass
    try:
        exc.close()
    except Exception:  # noqa: BLE001
        pass


def itunes_hires_url(url: str, size: int = 10000) -> str:
    """Rewrite an iTunes artwork URL to request a large resolution.

    Apple serves the largest available size up to the requested dimension.
    """
    # Typical: .../100x100bb.jpg  or  .../100x100bb.webp
    rewritten = re.sub(
        r'/\d+x\d+([a-z]*)\.(jpg|jpeg|png|webp)',
        f'/{size}x{size}\\1.\\2',
        url,
        count=1,
        flags=re.IGNORECASE,
    )
    return rewritten


def _best_deezer_result(
    results: list[dict[str, Any]],
    artist: str,
    album: str,
) -> dict[str, Any] | None:
    if not results:
        return None

    artist_l = artist.casefold().strip()
    album_l = album.casefold().strip()

    def score(item: dict[str, Any]) -> tuple[int, int, int]:
        ia = ((item.get('artist') or {}).get('name') or '').casefold()
        ic = (item.get('title') or '').casefold()
        if artist_l and artist_l == ia:
            artist_match = 2
        elif artist_l and (artist_l in ia or ia in artist_l):
            artist_match = 1
        else:
            artist_match = 0
        if album_l and album_l == ic:
            album_match = 2
        elif album_l and (album_l in ic or ic in album_l):
            album_match = 1
        else:
            album_match = 0
        return (artist_match + album_match, album_match, artist_match)

    return max(results, key=score)


def _best_itunes_result(
    results: list[dict[str, Any]],
    artist: str,
    album: str,
) -> dict[str, Any] | None:
    if not results:
        return None

    artist_l = artist.casefold().strip()
    album_l = album.casefold().strip()

    def score(item: dict[str, Any]) -> tuple[int, int, int]:
        ia = (item.get('artistName') or '').casefold()
        ic = (item.get('collectionName') or '').casefold()
        if artist_l and artist_l == ia:
            artist_match = 2
        elif artist_l and (artist_l in ia or ia in artist_l):
            artist_match = 1
        else:
            artist_match = 0
        if album_l and album_l == ic:
            album_match = 2
        elif album_l and (album_l in ic or ic in album_l):
            album_match = 1
        else:
            album_match = 0
        return (artist_match + album_match, album_match, artist_match)

    # iTunes already filtered by the search term; take the best-scoring hit.
    return max(results, key=score)


def _looks_like_image_url(url: str) -> bool:
    """Reject API/JSON endpoints that are not direct image files."""
    lower = url.casefold().split('?', 1)[0]
    if lower.endswith(('.json', '/release', '/release/')):
        return False
    # CAA release listing is JSON, not an image.
    if 'coverartarchive.org/release/' in lower and not lower.endswith(
        ('.jpg', '.jpeg', '.png', '.webp', '.gif')
    ):
        # Allow /front and /front-500 style image redirects.
        if re.search(r'/front(-\d+)?$', lower):
            return True
        if re.search(r'/\d+\.(jpg|jpeg|png|webp)$', lower):
            return True
        return False
    return True


def _guess_mime(data: bytes, content_type: str) -> str:
    if data[:3] == b'\xff\xd8\xff':
        return 'image/jpeg'
    if data[:8] == b'\x89PNG\r\n\x1a\n':
        return 'image/png'
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'image/webp'
    if data[:6] in (b'GIF87a', b'GIF89a'):
        return 'image/gif'
    if content_type.startswith('image/'):
        return content_type.split(';')[0].strip()
    return 'application/octet-stream'


def _image_size(data: bytes) -> tuple[int, int]:
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as im:
            return im.size
    except Exception:  # noqa: BLE001
        pass

    try:
        import gi

        gi.require_version('GdkPixbuf', '2.0')
        from gi.repository import GdkPixbuf, GLib

        loader = GdkPixbuf.PixbufLoader()
        loader.write(data)
        loader.close()
        pixbuf = loader.get_pixbuf()
        if pixbuf:
            return pixbuf.get_width(), pixbuf.get_height()
    except Exception:  # noqa: BLE001
        pass
    return 0, 0


def pixbuf_from_artwork(image: ArtworkImage, display_max: int = 256):
    """Create a GdkPixbuf for UI display (scaled down if needed)."""
    import gi

    gi.require_version('GdkPixbuf', '2.0')
    from gi.repository import GdkPixbuf, GLib

    loader = GdkPixbuf.PixbufLoader()
    try:
        loader.write(image.data)
        loader.close()
    except GLib.Error:
        try:
            loader.close()
        except GLib.Error:
            pass
        return None

    pixbuf = loader.get_pixbuf()
    if pixbuf is None:
        return None

    w, h = pixbuf.get_width(), pixbuf.get_height()
    if display_max > 0 and max(w, h) > display_max:
        scale = display_max / max(w, h)
        pixbuf = pixbuf.scale_simple(
            max(1, int(w * scale)),
            max(1, int(h * scale)),
            GdkPixbuf.InterpType.BILINEAR,
        )
    return pixbuf


def texture_from_artwork(image: ArtworkImage, display_max: int = 0):
    """Build a ``Gdk.Texture`` suitable for ``Gtk.Picture`` / ``Gtk.Image``.

    Prefers decoding the encoded image bytes (JPEG/PNG/…) via
    ``Gdk.Texture.new_from_bytes``, which is reliable on GTK 4. Falls back to
    a scaled pixbuf when needed.
    """
    import gi

    gi.require_version('Gdk', '4.0')
    from gi.repository import Gdk, GLib

    # Fast path: decode file bytes directly (no manual MemoryFormat).
    if image.data and display_max <= 0:
        try:
            return Gdk.Texture.new_from_bytes(GLib.Bytes.new(image.data))
        except GLib.Error:
            pass
        except Exception:  # noqa: BLE001
            pass

    pixbuf = pixbuf_from_artwork(
        image, display_max=display_max if display_max > 0 else 4096
    )
    if pixbuf is None:
        # Last try: raw decode without prior scale limit.
        if image.data:
            try:
                return Gdk.Texture.new_from_bytes(GLib.Bytes.new(image.data))
            except Exception:  # noqa: BLE001
                return None
        return None

    return _texture_from_pixbuf(pixbuf)


def cover_texture_from_artwork(image: ArtworkImage, edge: int = 240):
    """Square, center-cropped texture at *edge*×*edge* for a fixed cover widget.

    Pre-sizing avoids ``Gtk.Picture`` expanding to the image's natural size.
    """
    import gi

    gi.require_version('GdkPixbuf', '2.0')
    from gi.repository import GdkPixbuf, GLib

    # Decode at a generous cap so crop source stays sharp on HiDPI.
    pixbuf = pixbuf_from_artwork(image, display_max=max(edge * 3, 1024))
    if pixbuf is None:
        return texture_from_artwork(image, display_max=edge)

    w, h = pixbuf.get_width(), pixbuf.get_height()
    if w <= 0 or h <= 0:
        return None

    # Scale so the shorter side becomes *edge* (cover / crop-to-fill).
    scale = max(edge / w, edge / h)
    nw = max(edge, int(round(w * scale)))
    nh = max(edge, int(round(h * scale)))
    scaled = pixbuf.scale_simple(nw, nh, GdkPixbuf.InterpType.BILINEAR)
    if scaled is None:
        return None

    x = max(0, (scaled.get_width() - edge) // 2)
    y = max(0, (scaled.get_height() - edge) // 2)
    # new_subpixbuf shares pixels; copy so the texture owns stable data.
    cropped = scaled.new_subpixbuf(x, y, edge, edge)
    if cropped is None:
        return _texture_from_pixbuf(scaled)
    try:
        cropped = cropped.copy()
    except Exception:  # noqa: BLE001
        pass
    return _texture_from_pixbuf(cropped)


def apply_artwork_to_image(image_widget, image: ArtworkImage, display_max: int = 160) -> bool:
    """Show *image* on a ``Gtk.Image`` or ``Gtk.Picture`` as a paintable."""
    texture = texture_from_artwork(image, display_max=display_max)
    if texture is None:
        return False

    # Gtk.Picture
    if hasattr(image_widget, 'set_paintable') and image_widget.__class__.__name__ == 'Picture':
        image_widget.set_paintable(texture)
        return True

    # Gtk.Image
    if hasattr(image_widget, 'set_from_paintable'):
        image_widget.set_from_paintable(texture)
        return True

    if hasattr(image_widget, 'set_paintable'):
        image_widget.set_paintable(texture)
        return True
    return False


def apply_artwork_to_picture(
    picture_widget, image: ArtworkImage, *, edge: int = 240
) -> bool:
    """Show *image* full-bleed on a fixed-size ``Gtk.Picture`` cover."""
    texture = cover_texture_from_artwork(image, edge=edge)
    if texture is None:
        return False
    picture_widget.set_paintable(texture)
    return True


def _texture_from_pixbuf(pixbuf):
    """Build a ``Gdk.Texture`` from a pixbuf without deprecated APIs."""
    import gi

    gi.require_version('Gdk', '4.0')
    from gi.repository import Gdk, GLib

    width = pixbuf.get_width()
    height = pixbuf.get_height()
    stride = pixbuf.get_rowstride()
    # Copy pixel data; MemoryTexture keeps a reference to the bytes.
    data = bytes(pixbuf.get_pixels())
    gbytes = GLib.Bytes.new(data)

    if pixbuf.get_has_alpha():
        memory_format = Gdk.MemoryFormat.R8G8B8A8
    else:
        memory_format = Gdk.MemoryFormat.R8G8B8

    try:
        return Gdk.MemoryTexture.new(width, height, memory_format, gbytes, stride)
    except (TypeError, AttributeError, GLib.Error):
        if hasattr(Gdk.Texture, 'new_for_pixbuf'):
            return Gdk.Texture.new_for_pixbuf(pixbuf)
    return None
