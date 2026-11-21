# SPDX-License-Identifier: GPL-3.0-or-later
"""Metadata and artwork actions for Ready2RipWindow."""

from __future__ import annotations

from ready2rip.ui._window_shared import *  # noqa: F403

# NOTE: Composed onto Ready2RipWindow — methods use self and mixin globals.


class MetadataActionsMixin:

    def _artwork_source_options(self) -> ArtworkSourceOptions:
        s = self.store.get()
        return ArtworkSourceOptions(
            itunes=s.artwork_source_itunes,
            cover_art_archive=s.artwork_source_caa,
        )


    def _maybe_refetch_artwork(self) -> None:
        """Re-query covers when sources change and download is enabled."""
        if not self.store.get().fetch_artwork:
            return
        if self._album is None:
            return
        if not self._artwork_source_options().any_enabled:
            return
        self._start_artwork_fetch(self._album)


    def _on_lookup_disc(self, *_args) -> None:
        """MusicBrainz + gnudb lookup from the inserted disc’s identifiers."""
        if self._ripping or self._looking_up:
            return
        self._start_metadata_lookup(interactive=True)


    def _on_lookup_gnudb(self, *_args) -> None:
        """Query gnudb only for the inserted disc."""
        if self._ripping or self._looking_up:
            return
        if self._disc is None:
            self._toast('Insert a disc first')
            return
        self._start_metadata_lookup(
            interactive=True,
            use_musicbrainz=False,
            use_freedb=True,
        )


    def _on_lookup_mb_url(self, *_args) -> None:
        """Load metadata from a pasted MusicBrainz release URL or UUID."""
        if self._ripping or self._looking_up:
            return
        if self._disc is None:
            self._toast('Insert a disc first')
            return

        dialog = Adw.AlertDialog(
            heading='MusicBrainz release',
            body=(
                'Paste a musicbrainz.org/release/… link when automatic lookup '
                'chose the wrong album. Release-group links are not supported.'
            ),
        )
        dialog.add_response('cancel', 'Cancel')
        dialog.add_response('load', 'Load')
        dialog.set_response_appearance('load', Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response('load')
        dialog.set_close_response('cancel')

        entry = Gtk.Entry(
            placeholder_text='https://musicbrainz.org/release/…',
            hexpand=True,
        )
        entry.set_activates_default(True)
        dialog.set_extra_child(entry)

        def on_response(_dlg: Adw.AlertDialog, response: str) -> None:
            if response != 'load':
                return
            text = entry.get_text().strip()
            if not text:
                self._toast('Paste a MusicBrainz release link')
                return
            if parse_musicbrainz_release_id(text) is None:
                self._toast(
                    'Not a MusicBrainz release link '
                    '(use musicbrainz.org/release/…, not release-group)'
                )
                return
            self._start_mb_url_fetch(text)

        dialog.connect('response', on_response)
        dialog.present(self)


    def _start_metadata_lookup(
        self,
        *,
        interactive: bool,
        use_musicbrainz: bool | None = None,
        use_freedb: bool | None = None,
    ) -> None:
        if self._disc is None or self._ids is None or self._looking_up:
            return
        if self._setup_dialog_open:
            if not interactive:
                self._pending_auto_lookup = True
            return

        settings = self.store.get()
        use_mb = settings.use_musicbrainz if use_musicbrainz is None else use_musicbrainz
        use_fb = settings.use_freedb if use_freedb is None else use_freedb
        if not use_mb and not use_fb:
            if interactive:
                self._toast('Enable MusicBrainz or gnudb under Metadata options')
            return

        self._lookup_generation += 1
        generation = self._lookup_generation
        self._looking_up = True
        self._set_lookup_controls_sensitive(False)
        sources: list[str] = []
        if use_mb:
            sources.append('MusicBrainz')
        if use_fb:
            sources.append('gnudb')
        via = ' and '.join(sources) if sources else 'metadata'
        self._show_lookup_toast(f'Looking up metadata via {via}…')

        mb_id = self._ids.musicbrainz_discid
        freedb_id = self._ids.freedb_id
        offsets = self._ids.offsets
        track_count = self._ids.last_track

        def worker() -> None:
            results: list[AlbumMetadata] = []
            error: str | None = None
            try:
                results, provider_errors = lookup_metadata(
                    mb_id,
                    freedb_id,
                    use_musicbrainz=use_mb,
                    use_freedb=use_fb,
                    offsets=offsets,
                    track_count=track_count,
                )
                if not results and provider_errors:
                    error = '; '.join(provider_errors)
            except Exception as exc:
                results = []
                error = str(exc)

            def done() -> bool:
                self._on_lookup_finished(
                    generation,
                    results,
                    error,
                    interactive=interactive,
                )
                return GLib.SOURCE_REMOVE

            GLib.idle_add(done)

        threading.Thread(target=worker, daemon=True).start()


    def _start_mb_url_fetch(self, text: str) -> None:
        """Fetch a single release from a MusicBrainz link (background)."""
        if self._looking_up or self._setup_dialog_open:
            return

        self._lookup_generation += 1
        generation = self._lookup_generation
        self._looking_up = True
        self._set_lookup_controls_sensitive(False)
        self._show_lookup_toast('Loading MusicBrainz release…')

        discid = ''
        if self._ids is not None and self._ids.musicbrainz_discid:
            discid = self._ids.musicbrainz_discid
        track_count = self._disc.track_count if self._disc is not None else 0

        def worker() -> None:
            album: AlbumMetadata | None = None
            error: str | None = None
            try:
                album = fetch_album_from_musicbrainz_link(
                    text,
                    discid=discid,
                    preferred_track_count=track_count,
                )
            except ValueError as exc:
                error = str(exc)
            except Exception as exc:
                error = str(exc)

            def done() -> bool:
                self._on_mb_url_finished(generation, album, error)
                return GLib.SOURCE_REMOVE

            GLib.idle_add(done)

        threading.Thread(target=worker, daemon=True).start()


    def _on_lookup_finished(
        self,
        generation: int,
        results: list[AlbumMetadata],
        error: str | None,
        *,
        interactive: bool,
    ) -> None:
        if generation != self._lookup_generation:
            return

        self._looking_up = False
        self._dismiss_lookup_toast()
        self._set_lookup_controls_sensitive(self._disc is not None and not self._ripping)

        if error:
            self._toast(f'Lookup failed: {error}', timeout=6)
            return

        if not results:
            # Online miss — keep any CD-TEXT already applied, or try now.
            self._start_disc_meta_enrich(force_ui=True, toast_if_cdtext=interactive)
            if interactive:
                self._toast('No metadata matches for this disc')
            return

        if len(results) == 1 and not interactive:
            self._apply_album(results[0])
            self._toast(results[0].display_label)
            self._start_disc_meta_enrich(force_ui=False)
            return

        if len(results) == 1:
            self._apply_album(results[0])
            self._toast(f'Using {results[0].source}: {results[0].title}')
            self._start_disc_meta_enrich(force_ui=False)
            return

        # Multiple matches — always let the user pick when interactive,
        # and also when auto-lookup finds several candidates.
        self._show_picker(results)


    def _on_mb_url_finished(
        self,
        generation: int,
        album: AlbumMetadata | None,
        error: str | None,
    ) -> None:
        if generation != self._lookup_generation:
            return

        self._looking_up = False
        self._dismiss_lookup_toast()
        self._set_lookup_controls_sensitive(self._disc is not None and not self._ripping)

        if error or album is None:
            msg = error or 'Could not load that MusicBrainz release'
            self._toast(msg)
            return

        if self._disc is not None and album.tracks and len(album.tracks) != self._disc.track_count:
            self._toast(
                f'Loaded “{album.title}” ({len(album.tracks)} tracks on this '
                f'medium; disc has {self._disc.track_count})'
            )
        else:
            self._toast(f'Using MusicBrainz: {album.title}')

        self._apply_album(album)


    def _show_picker(self, candidates: list[AlbumMetadata]) -> None:
        disc_tracks = self._disc.track_count if self._disc is not None else 0
        dialog = MetadataPickerDialog(candidates, disc_track_count=disc_tracks)

        def on_closed(*_a) -> None:
            chosen = dialog.chosen
            if chosen is not None:
                self._apply_album(chosen)
                n = len(chosen.tracks) if chosen.tracks else 0
                if disc_tracks > 0 and n and n != disc_tracks:
                    self._toast(
                        f'Using {chosen.source}: {chosen.title} '
                        f'({n} tracks on this medium; disc has {disc_tracks})'
                    )
                else:
                    self._toast(f'Using {chosen.source}: {chosen.title}')
            elif self._album is None:
                self._toast(f'{len(candidates)} matches · none selected')

        dialog.connect('closed', on_closed)
        dialog.present(self)


    def _start_disc_meta_enrich(
        self,
        *,
        force_ui: bool,
        toast_if_cdtext: bool = False,
    ) -> None:
        """Background CD-TEXT / MCN / ISRC read; gap-fills ``self._album``."""
        disc = self._disc
        device = self._device
        if disc is None or not device:
            return
        track_numbers = [t.number for t in disc.tracks]
        album_snapshot = self._album

        def worker() -> None:
            try:
                enriched = enrich_album_from_disc(
                    album_snapshot,
                    device=device,
                    track_numbers=track_numbers,
                )
            except Exception:
                log.debug('Disc metadata enrich failed', exc_info=True)
                enriched = None

            def done() -> bool:
                if self._disc is not disc:
                    return GLib.SOURCE_REMOVE
                if enriched is None:
                    return GLib.SOURCE_REMOVE
                current = self._album
                if current is None or current is album_snapshot:
                    self._album = enriched
                else:
                    # Online lookup won the race — copy only empty gaps.
                    merged = gap_fill_album(current, enriched)
                    if merged is current:
                        return GLib.SOURCE_REMOVE
                    self._album = merged
                self._fill_album_edit_fields(self._album)
                self._update_album_header(self._disc, self._album)
                if self._track_checks:
                    self._refresh_track_fields_from_album()
                self._save_metadata_cache()
                src = (self._album.source if self._album else '') or ''
                if (force_ui or toast_if_cdtext) and src.startswith('cd-text'):
                    self._toast(self._format_metadata_origin(src))
                return GLib.SOURCE_REMOVE

            GLib.idle_add(done)

        threading.Thread(target=worker, daemon=True, name='ready2rip-cdmeta').start()


    def _apply_album(self, album: AlbumMetadata) -> None:
        # Keep disc track count aligned if lookup omitted some.
        self._album = album
        if self._disc is not None:
            self._ensure_album_tracks()
        self._clear_artwork()
        self._fill_album_edit_fields(self._album)
        self._update_album_header(self._disc, self._album)
        origin = self._format_metadata_origin(album.source)
        if origin != 'No metadata':
            self._toast(origin)
        if (
            self._disc is not None
            and self._track_checks
            and len(self._track_checks) == self._disc.track_count
        ):
            self._refresh_track_fields_from_album()
        else:
            selected = {num: check.get_active() for num, check in self._track_checks.items()}
            self._rebuild_track_list(self._disc)
            for num, was in selected.items():
                if num in self._track_checks:
                    self._track_checks[num].set_active(was)
        self._save_metadata_cache()
        if self.store.get().fetch_artwork and self._artwork_source_options().any_enabled:
            self._start_artwork_fetch(self._album)
        if self.store.get().fetch_lyrics:
            self._start_lyrics_fetch(self._album)


    def _start_lyrics_fetch(self, album: AlbumMetadata) -> None:
        """Background LRCLIB lookup for tracks missing lyrics."""
        if album is None or not self.store.get().fetch_lyrics:
            return
        self._lyrics_generation += 1
        generation = self._lyrics_generation
        disc = self._disc
        self._toast('Fetching lyrics…')

        def worker() -> None:
            from ready2rip.metadata.lyrics import LrclibClient

            count = 0
            error: str | None = None
            try:
                count = LrclibClient().fetch_album(album, disc, only_missing=True)
            except Exception as exc:
                error = str(exc)

            def done() -> bool:
                self._on_lyrics_finished(generation, album, count, error)
                return GLib.SOURCE_REMOVE

            GLib.idle_add(done)

        threading.Thread(target=worker, daemon=True, name='ready2rip-lyrics').start()


    def _on_lyrics_finished(
        self,
        generation: int,
        album: AlbumMetadata,
        count: int,
        error: str | None,
    ) -> None:
        if generation != self._lyrics_generation:
            return
        if self._album is not album:
            return
        if error:
            self._toast(f'Lyrics fetch failed: {error}')
            return
        self._save_metadata_cache()
        if count:
            self._toast(f'Lyrics found for {count} track(s)')
        else:
            self._toast('No lyrics found')


    def _choose_local_artwork(self) -> None:
        dialog = Gtk.FileDialog(title='Choose album artwork')
        filters = Gio.ListStore.new(Gtk.FileFilter)
        images = Gtk.FileFilter()
        images.set_name('Images')
        for mime in (
            'image/jpeg',
            'image/png',
            'image/webp',
            'image/gif',
            'image/*',
        ):
            images.add_mime_type(mime)
        filters.append(images)
        all_files = Gtk.FileFilter()
        all_files.set_name('All files')
        all_files.add_pattern('*')
        filters.append(all_files)
        dialog.set_filters(filters)
        dialog.set_default_filter(images)

        def on_done(dlg: Gtk.FileDialog, result) -> None:
            try:
                file = dlg.open_finish(result)
            except GLib.Error:
                return
            if file is None:
                return
            path = file.get_path()
            if not path:
                self._toast('Could not read that file path')
                return
            image = ArtworkFetcher().load_from_file(path)
            if image is None:
                self._toast('Could not load that image')
                return
            # Cancel any in-flight download so it does not overwrite local art.
            self._art_generation += 1
            self._artwork = image
            self._prepare_embed_artwork()
            self._show_artwork(image, announce=True)
            album = self._ensure_album_tracks()
            album.cover_url = path
            self._schedule_cache_save()

        dialog.open(self, None, on_done)


    def _clear_artwork(self) -> None:
        """Clear cover art from the UI."""
        self._art_generation += 1
        self._artwork = None
        self._artwork_embed = None
        self._artwork_embed_lossless = None
        self._show_placeholder_cover()
        self._sync_cover_action_sensitivity()


    def _start_artwork_fetch(self, album: AlbumMetadata) -> None:
        settings = self.store.get()
        if not settings.fetch_artwork:
            return
        sources = self._artwork_source_options()
        if not sources.any_enabled:
            return
        self._art_generation += 1
        generation = self._art_generation

        def worker() -> None:
            try:
                image = ArtworkFetcher().fetch_best(album, sources=sources)
                error = None
            except Exception as exc:
                image = None
                error = str(exc)

            def done() -> bool:
                self._on_artwork_finished(generation, image, error)
                return GLib.SOURCE_REMOVE

            GLib.idle_add(done)

        threading.Thread(target=worker, daemon=True).start()


    def _on_artwork_finished(
        self,
        generation: int,
        image: ArtworkImage | None,
        error: str | None,
    ) -> None:
        if generation != self._art_generation:
            return

        if error:
            self._toast(f'Artwork failed: {error}')
            return

        if image is None:
            self._toast('No artwork found')
            return

        self._artwork = image
        self._prepare_embed_artwork()
        self._show_artwork(image, announce=True)
        self._schedule_cache_save()


    def _prepare_embed_artwork(self) -> None:
        if self._artwork is None:
            self._artwork_embed = None
            self._artwork_embed_lossless = None
            return
        settings = self.store.get()
        fetcher = ArtworkFetcher()
        self._artwork_embed = fetcher.resize(self._artwork, settings.artwork_max_size)
        self._artwork_embed_lossless = fetcher.resize(
            self._artwork, settings.artwork_max_size_lossless
        )


    def _show_artwork(self, image: ArtworkImage, *, announce: bool = False) -> None:
        """Show *image* full-bleed in the cover frame."""
        size = getattr(self, '_cover_display_size', self._COVER_SIZE)
        self.cover_picture.set_content_fit(Gtk.ContentFit.COVER)
        self._apply_cover_display_size(size)

        if not apply_artwork_to_picture(self.cover_picture, image, edge=size):
            self._show_placeholder_cover()
            self._sync_cover_action_sensitivity()
            return

        self.cover_placeholder.set_visible(False)
        self._set_cover_frame_empty(False)
        self._update_cover_size_label()
        self._sync_cover_action_sensitivity()
        if announce:
            self._toast(self._format_art_origin(image.source))


    def _show_lookup_toast(self, title: str) -> None:
        """Bottom toast while metadata lookup is in flight (no auto-dismiss)."""
        self._dismiss_lookup_toast()
        toast = Adw.Toast(title=GLib.markup_escape_text(title))
        toast.set_timeout(0)
        try:
            toast.set_priority(Adw.ToastPriority.HIGH)
        except (AttributeError, TypeError):
            pass
        self._lookup_toast = toast
        self.toast_overlay.add_toast(toast)


    def _dismiss_lookup_toast(self) -> None:
        toast = self._lookup_toast
        self._lookup_toast = None
        if toast is not None:
            toast.dismiss()
