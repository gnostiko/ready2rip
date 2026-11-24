# SPDX-License-Identifier: GPL-3.0-or-later
"""Drive monitor and disc probing for Ready2RipWindow."""

from __future__ import annotations

from ready2rip.ui._window_shared import *  # noqa: F403

# NOTE: Composed onto Ready2RipWindow — methods use self and mixin globals.


class DriveMonitorMixin:

    def _start_async_probe(
        self,
        device: str,
        *,
        reason: str = 'refresh',
        from_monitor: bool = False,
    ) -> None:
        """Probe tray + TOC + drive info off the GTK thread, then update UI."""
        self._probe_generation += 1
        generation = self._probe_generation
        self._probe_in_flight = True
        # Keep the empty-state page honest while the worker reads the drive.
        if self.stack.get_visible_child_name() == 'empty':
            self.status_page.set_title('Reading disc')
            self.status_page.set_description(
                f'Reading the table of contents on {device}…\n'
                'This can take a few seconds after you insert a CD.'
            )

        def work() -> None:
            status: DriveStatus | None = None
            info: DiscInfo | None = None
            ids: DiscIdentifiers | None = None
            drive: DriveInfo | None = None
            err: str | None = None
            try:
                status = query_drive_status(device)
                # Identity only from udev/sysfs — never a second cdparanoia -Q
                # (TOC probe below already opens the drive once).
                try:
                    drive = probe_drive(device, allow_optical=False)
                except Exception as drive_exc:
                    log.debug('Drive identity probe failed: %s', drive_exc)
                    drive = DriveInfo(device=device, notes=[f'Probe failed: {drive_exc}'])

                if status.state not in (
                    DriveTrayState.TRAY_OPEN,
                    DriveTrayState.NO_DISC,
                    DriveTrayState.MISSING,
                ):
                    info = probe_disc(device)
                    if info is not None and info.tracks:
                        # TOC-only IDs — never re-open the device here.
                        ids = identifiers_from_disc(info)
            except Exception as exc:
                log.exception('Background disc probe failed (%s)', reason)
                err = str(exc)

            def finish() -> bool:
                self._probe_in_flight = False
                if generation != self._probe_generation:
                    return GLib.SOURCE_REMOVE
                try:
                    if err is not None or status is None:
                        self.status_page.set_title('Drive probe failed')
                        self.status_page.set_description(
                            f'{err or "Unknown error"}\nCheck Optical device path in Settings.'
                        )
                        if drive is not None:
                            self._drive_info = drive
                            self._rebuild_drive_rows(device, drive_info=drive, allow_optical=False)
                    else:
                        if drive is not None:
                            self._drive_info = drive
                        self._apply_probe_result(
                            device,
                            status,
                            info,
                            ids,
                            from_monitor=from_monitor,
                            drive_info=drive,
                        )
                    if reason == 'startup':
                        self._start_drive_monitor()
                except Exception:
                    log.exception('Disc probe UI update failed (%s)', reason)
                    if reason == 'startup':
                        self._start_drive_monitor()
                return GLib.SOURCE_REMOVE

            GLib.idle_add(finish)

        threading.Thread(target=work, daemon=True, name=f'ready2rip-probe-{reason}').start()


    def _drive_poll_interval_ms(self) -> int:
        """Poll faster while waiting for a disc; back off when stable."""
        if self.stack.get_visible_child_name() == 'empty':
            return 750
        state = self._last_tray_state
        if state in (
            DriveTrayState.NOT_READY,
            DriveTrayState.TRAY_OPEN,
            DriveTrayState.NO_DISC,
        ):
            return 750
        return 2000


    def _start_drive_monitor(self) -> None:
        """Poll tray/media state so we notice open trays and new discs."""
        if self._poll_id is not None:
            return
        self._poll_drive(force_refresh=False)
        self._schedule_drive_poll()


    def _schedule_drive_poll(self) -> None:
        if self._poll_id is not None:
            GLib.source_remove(self._poll_id)
            self._poll_id = None
        self._poll_id = GLib.timeout_add(self._drive_poll_interval_ms(), self._on_drive_poll)


    def _on_drive_poll(self) -> bool:
        self._poll_id = None
        self._poll_drive(force_refresh=False)
        self._schedule_drive_poll()
        return GLib.SOURCE_REMOVE


    def _poll_drive(self, *, force_refresh: bool) -> None:
        """Schedule a non-blocking tray check (never ioctl/cdparanoia on GTK thread)."""
        if self._ripping or self._probe_in_flight or self._poll_in_flight:
            return
        device = self.store.get().device or self._device or '/dev/sr0'
        self._device = device
        self._poll_generation += 1
        generation = self._poll_generation
        self._poll_in_flight = True

        def work() -> None:
            try:
                status = query_drive_status(device)
            except Exception as exc:
                log.debug('Drive poll failed: %s', exc)
                status = None

            def finish() -> bool:
                self._poll_in_flight = False
                if generation != self._poll_generation:
                    return GLib.SOURCE_REMOVE
                if status is None or self._ripping or self._probe_in_flight:
                    return GLib.SOURCE_REMOVE
                self._handle_drive_poll_result(device, status, force_refresh=force_refresh)
                return GLib.SOURCE_REMOVE

            GLib.idle_add(finish)

        threading.Thread(target=work, daemon=True, name='ready2rip-drive-poll').start()


    def _handle_drive_poll_result(
        self,
        device: str,
        status: DriveStatus,
        *,
        force_refresh: bool,
    ) -> None:
        """Apply tray poll results on the main thread (no device I/O)."""
        prev = self._last_tray_state
        self._drive_status = status
        state = status.state

        if prev != state or force_refresh:
            self._last_tray_state = state
            self._apply_drive_status_to_ui(status)

        empty = empty_tray_copy(state, device)
        if empty is not None:
            if prev is not state:
                if state is DriveTrayState.TRAY_OPEN or state is DriveTrayState.NO_DISC:
                    self._auto_rip_disc_key = None
                    self._auto_rip_pending = False
                title, description = empty
                self._clear_disc_ui_for_empty(
                    title=title,
                    description=description,
                )
            return

        if state is DriveTrayState.NOT_READY:
            if prev is not DriveTrayState.NOT_READY:
                if self.stack.get_visible_child_name() == 'empty':
                    self._set_status_page_icon(None)
                    self.status_page.set_title('Drive not ready')
                    self.status_page.set_description('Waiting for the drive to finish spinning up…')
            return

        if state is DriveTrayState.DISC_OK:
            need_load = (
                force_refresh
                or prev is not DriveTrayState.DISC_OK
                or self._disc is None
                or self.stack.get_visible_child_name() != 'disc'
            )
            if need_load and not self._probe_in_flight:
                self._refresh_disc(from_monitor=True)


    def _apply_drive_status_to_ui(self, status: DriveStatus) -> None:
        if self._drive_tray_row is not None:
            self._drive_tray_row.set_subtitle(status.label)
            tip = status.message if status.message else status.label
            self._drive_tray_row.set_tooltip_text(tip)
            return
        device = status.device or self._device or '/dev/sr0'
        self._rebuild_drive_rows(device, allow_optical=False)


    def _set_status_page_icon(self, icon_name: str | None) -> None:
        """Show a status-page icon, or hide it when *icon_name* is None."""
        if icon_name:
            apply_icon(self.status_page, icon_name, display=self.get_display())
        else:
            self.status_page.set_icon_name(None)


    def _clear_disc_ui_for_empty(
        self,
        *,
        title: str,
        description: str,
        icon_name: str | None = None,
    ) -> None:
        self._disc = None
        self._ids = None
        self._album = None
        self._clear_artwork()
        self.stack.set_visible_child_name('empty')
        self._set_rip_sensitive(False)
        self._set_lookup_controls_sensitive(False)
        self._set_status_page_icon(icon_name)
        self.status_page.set_title(title)
        self.status_page.set_description(description)
        self._rebuild_track_list(None)
        self._fill_album_edit_fields(None)
        self._rebuild_disc_rows(None, None)
        if self._drive_tray_row is not None and self._drive_status is not None:
            self._apply_drive_status_to_ui(self._drive_status)
        else:
            self._rebuild_drive_rows(
                self.store.get().device or self._device or '/dev/sr0',
                allow_optical=False,
            )
        self._update_album_header(None, None)


    def _disc_identity_key(self, info: DiscInfo, ids: DiscIdentifiers | None) -> str:
        if ids is not None:
            if ids.musicbrainz_discid:
                return f'mb:{ids.musicbrainz_discid}'
            if ids.freedb_id:
                return f'cddb:{ids.freedb_id}'
        total = sum(t.length_sectors for t in info.tracks)
        return f'toc:{info.device}:{info.track_count}:{total}'


    def _maybe_schedule_auto_rip(self, disc_key: str) -> None:
        settings = self.store.get()
        if not settings.auto_rip:
            return
        if self._ripping or self._auto_rip_pending:
            return
        if disc_key == self._auto_rip_disc_key:
            return
        self._auto_rip_pending = True
        self._toast('Auto-rip starting…')

        def _start() -> bool:
            self._auto_rip_pending = False
            if self._ripping:
                return GLib.SOURCE_REMOVE
            if self._disc is None or not self._disc.tracks:
                return GLib.SOURCE_REMOVE
            key = self._disc_identity_key(self._disc, self._ids)
            if key != disc_key:
                return GLib.SOURCE_REMOVE
            self._auto_rip_disc_key = disc_key
            self._start_rip()
            return GLib.SOURCE_REMOVE

        # Short grace so lookup can start; don't wait the full lookup.
        delay = 1200 if settings.auto_lookup_metadata else 400
        GLib.timeout_add(delay, _start)


    def _refresh_disc(self, *, from_monitor: bool = False) -> None:
        """Kick off a background probe (never blocks the GTK main loop)."""
        settings = self.store.get()
        device = settings.device or self._device or '/dev/sr0'
        self._device = device
        if not from_monitor and self.stack.get_visible_child_name() == 'empty':
            self.status_page.set_title('Looking for a disc')
            self.status_page.set_description(f'Looking for an audio CD on {device}…')
        self._start_async_probe(
            device,
            reason='refresh',
            from_monitor=from_monitor,
        )


    def _apply_probe_result(
        self,
        device: str,
        status: DriveStatus,
        info: DiscInfo | None,
        ids: DiscIdentifiers | None,
        *,
        from_monitor: bool = False,
        drive_info: DriveInfo | None = None,
    ) -> None:
        """Update UI from an already-completed drive/disc probe (main thread)."""
        settings = self.store.get()
        self._device = device
        self._drive_status = status
        self._last_tray_state = status.state
        if drive_info is not None:
            self._drive_info = drive_info
        self._rebuild_drive_rows(device, drive_info=self._drive_info, allow_optical=False)

        empty = empty_tray_copy(status.state, device)
        if empty is not None:
            title, description = empty
            self._clear_disc_ui_for_empty(
                title=title,
                description=description,
            )
            return

        self._disc = info
        self._album = None
        self._ids = None
        self._clear_artwork()

        if info is None or not info.tracks:
            self.stack.set_visible_child_name('empty')
            self._set_rip_sensitive(False)
            self._set_lookup_controls_sensitive(False)
            self._set_status_page_icon(None)
            if status.state is DriveTrayState.NOT_READY:
                self.status_page.set_title('Drive not ready')
                self.status_page.set_description(
                    'Waiting for the drive to finish loading the disc…'
                )
            else:
                from ready2rip.util import find_cdparanoia

                if find_cdparanoia() is None:
                    self.status_page.set_title('cd-paranoia required')
                    self.status_page.set_description(
                        'A disc is in the drive, but no paranoia binary was found.\n'
                        'Prefer libcdio-paranoia (Debian/Ubuntu: sudo apt install '
                        'cd-paranoia). Classic cdparanoia also works. '
                        'Then re-insert the disc or reopen ready2rip.'
                    )
                else:
                    self.status_page.set_title('No audio CD detected')
                    self.status_page.set_description(
                        f'A disc is present on {device}, but no audio tracks were found. '
                        'Insert an audio CD.'
                    )
            self._album = None
            self._rebuild_track_list(None)
            self._fill_album_edit_fields(None)
            self._rebuild_disc_rows(None, None)
            self._update_album_header(None, None)
            return

        self._ids = ids if ids is not None else identifiers_from_disc(info)
        self.stack.set_visible_child_name('disc')
        # Switching to the disc page focuses the first EntryRow otherwise.
        self._defer_clear_window_focus()
        self._set_rip_sensitive(not self._ripping)
        self._set_lookup_controls_sensitive(not self._ripping and not self._looking_up)

        # Restore cached metadata for this disc when available.
        cached_album, cached_art = self._meta_cache.load(info, self._ids)
        if cached_album is not None and self._meta_cache.has_useful_metadata(cached_album):
            self._album = cached_album
            restored = True
        else:
            self._album = self._blank_album_for_disc(info)
            restored = False

        self._rebuild_disc_rows(info, self._ids)
        self._fill_album_edit_fields(self._album)
        self._rebuild_track_list(info)
        self._update_album_header(info, self._album)

        if cached_art is not None:
            self._art_generation += 1
            self._artwork = cached_art
            self._prepare_embed_artwork()
            self._show_artwork(cached_art, announce=True)

        if restored:
            origin = self._format_metadata_origin(
                self._album.source if self._album is not None else None
            )
            if origin != 'No metadata':
                self._toast(origin)
            else:
                self._toast(
                    f'Restored cached metadata · {info.track_count} tracks',
                    timeout=3,
                )
        else:
            n_data = len(info.data_tracks)
            if n_data:
                self._toast(
                    f'Found {info.track_count} audio tracks · {n_data} data',
                    timeout=3,
                )
            else:
                self._toast(f'Found {info.track_count} audio tracks', timeout=3)

        # Seed / gap-fill from CD-TEXT, MCN, and ISRC on the disc.
        self._start_disc_meta_enrich(force_ui=not restored)

        # Only auto-lookup when we have nothing useful cached.
        if settings.auto_lookup_metadata and not restored:
            self._start_metadata_lookup(interactive=False)

        # Auto-rip when a new disc is detected (especially via monitor).
        disc_key = self._disc_identity_key(info, self._ids)
        if settings.auto_rip:
            self._maybe_schedule_auto_rip(disc_key)


    def _rebuild_disc_rows(
        self,
        info: DiscInfo | None,
        ids: DiscIdentifiers | None,
    ) -> None:
        for row in self._disc_rows:
            self.disc_group.remove(row)
        self._disc_rows.clear()

        if info is None:
            self.disc_group.set_description(None)
            row = Adw.ActionRow(
                title='Status',
                subtitle='No disc',
            )
            self.disc_group.add(row)
            self._disc_rows.append(row)
            return

        n_data = len(info.data_tracks)
        if n_data:
            self.disc_group.set_description(
                f'{info.track_count} audio · {n_data} data · {info.device}'
            )
        else:
            self.disc_group.set_description(f'{info.track_count} tracks · {info.device}')

        rows = [
            ('Device', info.device),
            ('Audio tracks', str(info.track_count)),
        ]
        if n_data:
            data_lab = ', '.join(
                f'#{d.number} {d.cue_mode}' + (f' (session {d.session})' if d.session > 1 else '')
                for d in info.data_tracks
            )
            rows.append(('Data tracks', data_lab))
        if ids and ids.musicbrainz_discid:
            rows.append(('MusicBrainz DiscID', ids.musicbrainz_discid))
        if ids and ids.freedb_id:
            rows.append(('gnudb disc ID', ids.freedb_id))

        total_sectors = sum(t.length_sectors for t in info.tracks)
        minutes, seconds = divmod(round(total_sectors / 75.0), 60)
        rows.append(('Duration', f'{minutes}:{seconds:02d}'))
        rows.append(('Total sectors', str(total_sectors)))

        for title, subtitle in rows:
            row = Adw.ActionRow(title=title, subtitle=subtitle)
            row.set_tooltip_text(subtitle)
            self.disc_group.add(row)
            self._disc_rows.append(row)


    def _rebuild_drive_rows(
        self,
        device: str,
        *,
        drive_info: DriveInfo | None = None,
        allow_optical: bool = False,
    ) -> None:
        """Rebuild Technical → Drive rows.

        Default *allow_optical=False*: never run cdparanoia/ioctls on the GTK
        thread (that froze the UI while “reading disc”). Optical identity is
        filled only from a background probe when explicitly requested.
        """
        for row in self._drive_rows:
            self.drive_group.remove(row)
        self._drive_rows.clear()
        self._drive_tray_row = None

        settings = self.store.get()
        info = drive_info if drive_info is not None else self._drive_info
        if info is None or info.device != device:
            try:
                info = probe_drive(device, allow_optical=allow_optical)
            except Exception as exc:
                info = DriveInfo(device=device, notes=[f'Probe failed: {exc}'])
            self._drive_info = info

        self.drive_group.set_description(info.display_name or device)

        # Prefer cached tray status; never ioctl here on the main thread.
        st = self._drive_status
        if st is not None:
            tray_row = Adw.ActionRow(title='Tray / media', subtitle=st.label)
            if st.message and st.message != st.state.value:
                tray_row.set_tooltip_text(st.message)
            else:
                tray_row.set_tooltip_text(st.label)
        else:
            tray_row = Adw.ActionRow(
                title='Tray / media',
                subtitle='Checking…',
            )
        self.drive_group.add(tray_row)
        self._drive_rows.append(tray_row)
        self._drive_tray_row = tray_row

        for title, subtitle in info.as_rows():
            row = Adw.ActionRow(title=title, subtitle=subtitle)
            row.set_tooltip_text(subtitle)
            self.drive_group.add(row)
            self._drive_rows.append(row)

        offset_sub = f'{settings.drive_sample_offset} samples'
        if settings.drive_offset_configured:
            offset_sub += f' · saved for {settings.drive_offset_device or device}'
        else:
            offset_sub += ' · not calibrated'
        offset_row = Adw.ActionRow(title='Sample offset', subtitle=offset_sub)
        self.drive_group.add(offset_row)
        self._drive_rows.append(offset_row)


    def present_drive_setup(self, *_args) -> None:
        """Show the sample-offset dialog; defer metadata until it closes."""
        self.set_drive_setup_open(True)
        dialog = DriveSetupDialog(self.store)

        def on_closed(*_a) -> None:
            self.sync_options_from_store()
            self.set_drive_setup_open(False)
            pending = self._pending_rip_after_setup
            self._pending_rip_after_setup = False
            if pending:
                if not needs_drive_setup(self.store):
                    self._start_rip_after_offset_check()
                else:
                    self._toast('Calibrate the sample offset before ripping')

        dialog.connect('closed', on_closed)
        dialog.present(self)


    def set_drive_setup_open(self, is_open: bool) -> None:
        """Block metadata lookup while the drive-setup popup is showing."""
        was_open = self._setup_dialog_open
        self._setup_dialog_open = is_open
        if was_open and not is_open and self._pending_auto_lookup:
            self._pending_auto_lookup = False
            self._start_metadata_lookup(interactive=False)
