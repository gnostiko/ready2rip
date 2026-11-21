# SPDX-License-Identifier: GPL-3.0-or-later
"""Rip lifecycle actions for Ready2RipWindow."""

from __future__ import annotations

from ready2rip.ui._window_shared import *  # noqa: F403

# NOTE: Composed onto Ready2RipWindow — methods use self and mixin globals.


# Confirm Stop only after the rip has been running this long.
_CANCEL_RIP_CONFIRM_AFTER_S = 3.0

_RIP_STATUS_FALLBACK = {
    RipState.REPLAYGAIN: 'Applying ReplayGain…',
    RipState.ENCODING: 'Encoding…',
    RipState.VERIFYING: 'Verifying…',
    RipState.PREPARING: 'Preparing…',
    RipState.RIPPING: 'Extracting…',
}


class RipActionsMixin:

    def _on_eject_clicked(self, *_args) -> None:
        if self._ripping:
            self._toast('Cannot eject while ripping')
            return
        self._eject_disc()


    def _on_rip_clicked(self, *_args) -> None:
        if self._ripping:
            self._request_cancel_rip()
            return
        self._start_rip()


    def _request_cancel_rip(self) -> None:
        """Stop the rip; confirm once it has been running a few seconds."""
        if not self._ripping:
            return
        elapsed = time.monotonic() - self._rip_started_at
        if elapsed < _CANCEL_RIP_CONFIRM_AFTER_S:
            self._cancel_rip_now()
            return
        if self._cancel_rip_dialog is not None:
            return
        dialog = Adw.AlertDialog(
            heading='Stop ripping?',
            body=(
                'Extraction or encoding is already in progress. '
                'Stopping now may leave incomplete files.'
            ),
        )
        dialog.add_response('keep', 'Keep ripping')
        dialog.add_response('stop', 'Stop')
        dialog.set_response_appearance('stop', Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response('keep')
        dialog.set_close_response('keep')

        def on_response(_dlg: Adw.AlertDialog, response: str) -> None:
            self._cancel_rip_dialog = None
            if response == 'stop' and self._ripping:
                self._cancel_rip_now()

        self._cancel_rip_dialog = dialog
        dialog.connect('response', on_response)
        dialog.present(self)


    def _cancel_rip_now(self) -> None:
        """Request cancellation of the active rip (no confirm)."""
        if self._rip_engine is not None:
            self._rip_engine.cancel()
        self.rip_title_label.set_label('Ripping disc')
        self.rip_status_label.set_label('Stopping extraction…')


    def _selected_track_numbers(self) -> list[int]:
        selected = [num for num, check in sorted(self._track_checks.items()) if check.get_active()]
        if selected:
            return selected
        if self._disc is not None:
            return [t.number for t in self._disc.tracks]
        return []


    def _start_rip(self) -> None:
        if self._disc is None or not self._disc.tracks:
            self._toast('No disc to rip')
            return

        track_numbers = self._selected_track_numbers()
        if not track_numbers:
            self._toast('Select at least one track to rip')
            return

        settings = self.store.get()
        if needs_drive_setup(self.store):
            if (
                settings.verify_accuraterip
                or settings.verify_ctdb
                or settings.submit_ctdb
            ):
                # Hard gate: AR/CTDB verify and CTDB submit need a measured offset.
                self._require_offset_calibration()
                return
            # Verification off — offset 0 is fine for an unverified rip.
            self._start_rip_after_offset_check()
            return

        self._start_rip_after_offset_check()


    def _require_offset_calibration(self) -> None:
        """Block rip until the drive sample offset is calibrated."""
        settings = self.store.get()
        targets: list[str] = []
        if settings.verify_accuraterip:
            targets.append('AccurateRip')
        if settings.verify_ctdb:
            targets.append('CTDB')
        if settings.submit_ctdb and 'CTDB' not in targets:
            targets.append('CTDB submit')
        which = ' and '.join(targets) if targets else 'verification'
        dialog = Adw.AlertDialog(
            heading='Sample offset required',
            body=(
                f'{which} is on, so this drive needs a measured sample '
                f'offset before ripping. Detect the offset now, or turn off '
                f'{which} in Settings.'
            ),
        )
        dialog.add_response('cancel', 'Cancel')
        dialog.add_response('detect', 'Detect…')
        dialog.set_response_appearance('detect', Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response('detect')
        dialog.set_close_response('cancel')

        def on_response(_dlg: Adw.AlertDialog, response: str) -> None:
            if response == 'detect':
                self._pending_rip_after_setup = True
                self.present_drive_setup()

        dialog.connect('response', on_response)
        dialog.present(self)


    def _start_rip_after_offset_check(self) -> None:
        settings = self.store.get()
        track_numbers = self._selected_track_numbers()
        if not track_numbers:
            self._toast('Select at least one track to rip')
            return

        # Full-res for folder cover; separate embed sizes for lossy / lossless.
        folder_art = self._artwork
        need_art = settings.wants_embed_artwork() or settings.fetch_artwork
        if need_art and folder_art is None and self._album is not None:
            self._show_progress_panel(title='Ripping disc')
            self.rip_status_label.set_label('Fetching artwork…')
            self._set_progress_fraction(0.0)
            try:
                full = ArtworkFetcher().fetch_best(
                    self._album,
                    sources=self._artwork_source_options(),
                )
                if full is not None:
                    self._artwork = full
                    self._prepare_embed_artwork()
                    folder_art = full
                    self._show_artwork(full, announce=True)
            except Exception:
                pass
        elif folder_art is not None and (
            self._artwork_embed is None or self._artwork_embed_lossless is None
        ):
            self._prepare_embed_artwork()

        try:
            from ready2rip.rip.job_build import job_from_settings
            from ready2rip.rip.plan import conflicting_paths

            job = job_from_settings(
                settings,
                track_numbers=track_numbers,
                device=settings.device or self._device or '/dev/sr0',
                disc=self._disc,
                album=self._album,
                folder_artwork=folder_art,
                artwork_embed=self._artwork_embed,
                artwork_embed_lossless=self._artwork_embed_lossless,
            )
        except ValueError as exc:
            self._toast(str(exc))
            self._set_ripping_ui(False)
            self._ripping = False
            return

        policy = normalize_existing_files_policy(job.existing_files)
        if policy == 'ask':
            planner = RipEngine()
            conflicts = conflicting_paths(planner.planned_audio_paths(job))
            if conflicts:
                self._confirm_overwrite_then_rip(job, conflicts)
                return

        self._begin_rip_job(job)


    def _confirm_overwrite_then_rip(self, job: RipJob, conflicts: list[Path]) -> None:
        """Ask before replacing existing outputs (Existing files → Ask)."""
        n = len(conflicts)
        sample = ', '.join(p.name for p in conflicts[:3])
        extra = f' and {n - 3} more' if n > 3 else ''
        dialog = Adw.AlertDialog(
            heading='Replace existing files?',
            body=(
                f'{n} output file{"s" if n != 1 else ""} already exist'
                f'{f" ({sample}{extra})" if sample else ""}. '
                f'Overwrite them, or cancel and change Existing files in Settings.'
            ),
        )
        dialog.add_response('cancel', 'Cancel')
        dialog.add_response('overwrite', 'Overwrite')
        dialog.set_response_appearance('overwrite', Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response('cancel')
        dialog.set_close_response('cancel')

        def on_response(_dlg: Adw.AlertDialog, response: str) -> None:
            if response != 'overwrite':
                return
            job.existing_files = 'overwrite'
            self._begin_rip_job(job)

        dialog.connect('response', on_response)
        dialog.present(self)


    def _begin_rip_job(self, job: RipJob) -> None:
        self._ripping = True
        self._rip_started_at = time.monotonic()
        self._rip_engine = RipEngine()
        self._set_ripping_ui(True)
        fmt = (job.encode_format or 'flac').upper()
        n = len(job.track_numbers)
        self.rip_title_label.set_label('Ripping disc')
        if job.copy_image:
            self.rip_status_label.set_label(f'Starting disc image ({fmt})…')
        else:
            self.rip_status_label.set_label(f'Starting {fmt} · {n} track{"s" if n != 1 else ""}…')
        self._banner_hold = False
        self._set_progress_fraction(0.0)

        engine = self._rip_engine

        def on_progress(progress: RipProgress) -> None:
            self._queue_rip_progress(progress)

        def worker() -> None:
            result = engine.run(job, on_progress=on_progress)

            def done() -> bool:
                self._on_rip_finished(result)
                return GLib.SOURCE_REMOVE

            GLib.idle_add(done)

        threading.Thread(target=worker, daemon=True).start()


    def _cancel_progress_hide(self) -> None:
        if self._progress_hide_id is not None:
            GLib.source_remove(self._progress_hide_id)
            self._progress_hide_id = None


    def _set_progress_fraction(self, fraction: float) -> None:
        self._set_banner_target(max(0.0, min(1.0, fraction)))


    def _show_progress_panel(self, *, title: str | None = None) -> None:
        self._cancel_progress_hide()
        self._banner_hold = False
        self.rip_title_label.set_label(title or 'Ripping disc')
        # Reveal the ToolbarView bottom bar first so content slides up cleanly
        # without a pre-existing separator line sitting at the window edge.
        self.toolbar_view.set_reveal_bottom_bars(True)
        self.rip_revealer.set_reveal_child(True)


    def _hide_progress_panel(self, delay_ms: int = 0) -> None:
        self._cancel_progress_hide()

        def _hide_now() -> None:
            self.rip_revealer.set_reveal_child(False)
            # Drop the bottom bar after the slide animation so the separator
            # does not linger as a lone hairline.
            GLib.timeout_add(240, self._finish_hide_bottom_bar)

        if delay_ms <= 0:
            _hide_now()
            return

        def _hide() -> bool:
            self._progress_hide_id = None
            if not self._ripping and not self._banner_hold:
                _hide_now()
            return GLib.SOURCE_REMOVE

        self._progress_hide_id = GLib.timeout_add(delay_ms, _hide)


    def _finish_hide_bottom_bar(self) -> bool:
        if not self._ripping and not self._banner_hold and not self.rip_revealer.get_reveal_child():
            self.toolbar_view.set_reveal_bottom_bars(False)
            self._reset_banner_fill()
        return GLib.SOURCE_REMOVE


    def _set_banner_target(self, fraction: float) -> None:
        self._banner_target = max(0.0, min(1.0, fraction))
        self._ensure_banner_tick()


    def _ensure_banner_tick(self) -> None:
        if self._banner_tick_id is not None:
            return
        self._banner_last_us = 0
        self._banner_tick_id = self.add_tick_callback(self._on_banner_tick)


    def _on_banner_tick(self, _widget, clock) -> bool:
        frame = clock.get_frame_time()
        if self._banner_last_us == 0:
            self._banner_last_us = frame
            self._apply_banner_fill()
            return GLib.SOURCE_CONTINUE
        dt = min(0.05, (frame - self._banner_last_us) / 1_000_000)
        self._banner_last_us = frame
        t = min(1.0, dt * 12.0)
        self._banner_shown += (self._banner_target - self._banner_shown) * t
        if abs(self._banner_target - self._banner_shown) < 0.0008:
            self._banner_shown = self._banner_target
            self._apply_banner_fill()
            self._banner_tick_id = None
            return GLib.SOURCE_REMOVE
        self._apply_banner_fill()
        return GLib.SOURCE_CONTINUE


    def _apply_banner_fill(self) -> None:
        # 0.1% steps — each finished track is a clear jump on the fill.
        q = round(self._banner_shown * 1000.0) / 1000.0
        if q == self._banner_css_frac:
            return
        self._banner_css_frac = q
        self._banner_provider.load_from_string(banner_fill_css(q))


    def _reset_banner_fill(self) -> bool:
        if self._ripping or self._banner_hold:
            return GLib.SOURCE_REMOVE
        self._banner_shown = 0.0
        self._banner_target = 0.0
        self._banner_css_frac = -1.0
        if self._banner_tick_id is not None:
            self.remove_tick_callback(self._banner_tick_id)
            self._banner_tick_id = None
        self._apply_banner_fill()
        return GLib.SOURCE_REMOVE


    def _flash(self, kind: str) -> None:
        if self._flash_id is not None:
            GLib.source_remove(self._flash_id)
            self._flash_id = None
        if kind == 'error':
            self.flash_veil.add_css_class('error')
        else:
            self.flash_veil.remove_css_class('error')
        self.flash_veil.add_css_class('visible')
        self._flash_id = GLib.timeout_add(350, self._hide_flash)


    def _flash_success(self) -> None:
        self._flash('success')


    def _flash_error(self) -> None:
        self._flash('error')


    def _hide_flash(self) -> bool:
        self._flash_id = None
        self.flash_veil.remove_css_class('visible')
        # Drop error tint after the fade so the next success flash stays green.
        GLib.timeout_add(220, self._clear_flash_error_class)
        return GLib.SOURCE_REMOVE


    def _clear_flash_error_class(self) -> bool:
        if 'visible' not in self.flash_veil.get_css_classes():
            self.flash_veil.remove_css_class('error')
        return GLib.SOURCE_REMOVE


    def _release_banner(self) -> bool:
        if self._ripping:
            self._banner_hold = False
            return GLib.SOURCE_REMOVE
        self._banner_hold = False
        self._hide_progress_panel()
        GLib.timeout_add(220, self._reset_banner_fill)
        return GLib.SOURCE_REMOVE


    def _set_lookup_controls_sensitive(self, sensitive: bool) -> None:
        """Enable/disable the Lookup menu (disc ID, gnudb, MusicBrainz link)."""
        self.lookup_button.set_sensitive(sensitive)


    def _set_action_enabled(self, name: str, enabled: bool) -> None:
        action = self.lookup_action(name)
        if action is not None:
            action.set_enabled(enabled)


    def _set_rip_sensitive(self, sensitive: bool) -> None:
        self.rip_button.set_sensitive(sensitive)
        self._set_action_enabled('rip', sensitive)


    def _set_track_select_sensitive(self, sensitive: bool) -> None:
        """Enable/disable left-side track select checkboxes (select-all + per track)."""
        if self._track_select_all is not None:
            self._track_select_all.set_sensitive(sensitive)
        for check in self._track_checks.values():
            check.set_sensitive(sensitive)


    def _set_ripping_ui(self, active: bool) -> None:
        idle = not active
        if active:
            self._show_progress_panel(title='Ripping disc')
            if self._tracks_editing:
                self._tracks_editing = False
                self._apply_tracks_edit_mode()

        self.eject_button.set_sensitive(idle)
        self._set_action_enabled('eject', idle)
        self._set_lookup_controls_sensitive(idle and self._disc is not None)
        self.settings_button.set_sensitive(idle)
        self._set_action_enabled('preferences', idle)
        if self._track_edit_button is not None:
            self._track_edit_button.set_sensitive(idle)
        if self._track_artist_button is not None:
            self._track_artist_button.set_sensitive(idle)
        self._set_track_select_sensitive(idle)

        self.search_art_button.set_sensitive(idle)
        self.choose_art_button.set_sensitive(idle)
        if idle:
            self._sync_cover_action_sensitivity()
        else:
            self.clear_art_button.set_sensitive(False)
        self._set_album_fields_editable(idle)

        if active:
            self.rip_button.set_label('Cancel')
            self._set_rip_sensitive(True)
            self.rip_button.remove_css_class('suggested-action')
            self.rip_button.add_css_class('destructive-action')
        else:
            self.rip_button.set_label('Rip')
            self.rip_button.remove_css_class('destructive-action')
            self.rip_button.add_css_class('suggested-action')
            self._set_rip_sensitive(self._disc is not None and bool(self._disc.tracks))


    def _queue_rip_progress(self, progress: RipProgress) -> None:
        """Coalesce extract/encode progress onto the next GTK idle."""
        with self._progress_queue_lock:
            self._pending_progress = progress
            if self._progress_idle_id is not None:
                return
            # DEFAULT (not DEFAULT_IDLE) so the banner keeps up under UI load.
            self._progress_idle_id = GLib.idle_add(
                self._flush_rip_progress,
                priority=GLib.PRIORITY_DEFAULT,
            )


    def _flush_rip_progress(self) -> bool:
        with self._progress_queue_lock:
            self._progress_idle_id = None
            progress = self._pending_progress
            self._pending_progress = None
        if progress is not None:
            self._on_rip_progress(progress)
        return GLib.SOURCE_REMOVE


    def _on_rip_progress(self, progress: RipProgress) -> None:
        if not self._ripping:
            return
        self.rip_title_label.set_label('Ripping disc')
        self._set_progress_fraction(progress.fraction)
        if progress.message:
            self.rip_status_label.set_label(progress.message)
            if progress.message.startswith('Overread:'):
                self._update_overread_row()
        elif (fallback := _RIP_STATUS_FALLBACK.get(progress.state)) is not None:
            self.rip_status_label.set_label(fallback)


    def _on_rip_finished(self, result: RipResult) -> None:
        self._ripping = False
        self._rip_engine = None
        self._rip_started_at = 0.0
        if self._cancel_rip_dialog is not None:
            try:
                self._cancel_rip_dialog.force_close()
            except Exception:
                pass
            self._cancel_rip_dialog = None
        self._set_ripping_ui(False)

        if result.cancelled:
            self.rip_title_label.set_label('Ripping disc')
            self.rip_status_label.set_label('Rip cancelled')
            self._set_progress_fraction(0.0)
            self._flash_error()
            self._hide_progress_panel(delay_ms=2500)
            self._toast('Rip cancelled')
            return

        # Per-track AR/CTDB rows matter most on archival fail.
        if result.accuraterip or result.ctdb:
            self._apply_verify_results_to_rows(
                ar_results=result.accuraterip,
                ctdb_results=result.ctdb,
            )

        n = len(result.output_files)
        if not result.success:
            err = result.error or 'Rip failed'
            if n:
                status = f'{err} · {n} file(s) kept'
                toast = f'Archival checks failed · {n} file(s) kept'
            else:
                status = err
                toast = err
            self.rip_title_label.set_label('Ripping disc')
            self.rip_status_label.set_label(status)
            self._set_progress_fraction(1.0 if n else 0.0)
            self._show_progress_panel(title='Ripping disc')
            self._flash_error()
            self._hide_progress_panel(delay_ms=8000)
            self._toast(toast, timeout=6)
            self._schedule_auto_eject_if_enabled()
            return

        dest_dirs = sorted(
            {str(p.parent) for p in result.output_files if p.suffix.lower() != '.cue'}
        )
        if not dest_dirs and result.album_dir is not None:
            dest_dirs = [str(result.album_dir)]
        dest = (
            dest_dirs[0]
            if len(dest_dirs) == 1
            else (f'{len(dest_dirs)} folders' if dest_dirs else 'output folder')
        )
        ar_bits = []
        if result.outcome_summary:
            ar_bits.append(result.outcome_summary)
        elif result.secure_ok:
            ar_bits.append('Secure OK')
        if result.htoa_ripped:
            ar_bits.append('HTOA')
        if any(p.suffix.lower() == '.bin' for p in result.output_files):
            ar_bits.append('data')
        if result.cover_path is not None:
            ar_bits.append('cover saved')
        if result.accuraterip and 'AR ' not in (result.outcome_summary or ''):
            matches = sum(
                1 for r in result.accuraterip if r.confidence == AccurateRipConfidence.MATCH
            )
            ar_bits.append(f'AR {matches}/{len(result.accuraterip)}')
        if result.ctdb and 'CTDB ' not in (result.outcome_summary or ''):
            matches = sum(1 for r in result.ctdb if r.confidence == CtdbConfidence.MATCH)
            hard_miss = sum(
                1 for r in result.ctdb if r.confidence == CtdbConfidence.MISMATCH
            )
            if matches == len(result.ctdb):
                ar_bits.append(f'CTDB {matches}/{len(result.ctdb)}')
            elif hard_miss:
                ar_bits.append(f'CTDB {matches}/{len(result.ctdb)} ({hard_miss} mismatch)')
            elif matches:
                ar_bits.append(f'CTDB {matches}/{len(result.ctdb)}')
            else:
                ar_bits.append('CTDB unverified')
        intentional_burst = bool(self.store.get().burst_mode)
        if result.burst_tracks and intentional_burst:
            n_burst = len(result.burst_tracks)
            if 'Burst mode' not in (result.outcome_summary or ''):
                ar_bits.append(
                    f'burst mode (-Z) · {n_burst} track{"s" if n_burst != 1 else ""}'
                )
        if result.log_path is not None:
            ar_bits.append('log saved')
        detail = f'{n} file{"s" if n != 1 else ""} saved to {dest}'
        if ar_bits:
            detail = f'{detail} · {", ".join(ar_bits)}'
        self.rip_title_label.set_label('Ripping disc')
        self.rip_status_label.set_label(detail)
        self._set_progress_fraction(1.0)
        self._flash_success()
        self._banner_hold = True
        self.toolbar_view.set_reveal_bottom_bars(True)
        self.rip_revealer.set_reveal_child(True)
        GLib.timeout_add(560, self._release_banner)
        toast = self._rip_success_toast(result, n=n, intentional_burst=intentional_burst)
        self._toast(toast, timeout=5 if 'not in' in toast else 4)

        self._schedule_auto_eject_if_enabled()

    def _schedule_auto_eject_if_enabled(self) -> None:
        """Eject after a finished rip when Auto-eject is on (success or fail)."""
        if not self.store.get().auto_eject:
            return
        # Give the kernel a moment to release the optical device after the
        # rip thread finishes; eject_drive also retries on “busy”.
        GLib.timeout_add(2000, self._do_auto_eject)


    @staticmethod
    def _rip_success_toast(
        result: RipResult,
        *,
        n: int,
        intentional_burst: bool,
    ) -> str:
        """Toast that distinguishes Secure OK from DB-verified."""
        summary = (result.outcome_summary or '').strip()
        if summary:
            return f'{summary} · {n} file(s)'
        if intentional_burst and result.burst_tracks:
            n_burst = len(result.burst_tracks)
            return (
                f'Burst mode · {n} file(s) · '
                f'{n_burst} track{"s" if n_burst != 1 else ""} (-Z)'
            )
        if result.secure_ok and not result.db_verified:
            if result.accuraterip or result.ctdb:
                return f'Secure OK · not DB-verified · {n} file(s)'
            return f'Secure OK · {n} file(s)'
        if result.secure_ok and result.db_verified:
            return f'Secure OK · DB verified · {n} file(s)'
        if result.db_verified:
            return f'DB verified · {n} file(s)'
        return f'Rip complete · {n} file(s)'


    def _eject_disc(self) -> None:
        """Open the tray / eject media on the configured optical device."""
        device = self.store.get().device or self._device or '/dev/sr0'
        self.eject_button.set_sensitive(False)

        def work() -> None:
            ok, message = eject_drive(device)

            def done() -> bool:
                self.eject_button.set_sensitive(not self._ripping)
                if ok:
                    self._toast('Disc ejected')
                    self._last_tray_state = DriveTrayState.TRAY_OPEN
                    self._clear_disc_ui_for_empty(
                        title='Tray open',
                        description=('Disc ejected. Close the tray or insert another CD.'),
                    )
                else:
                    self._toast(f'Eject failed: {message}')
                return GLib.SOURCE_REMOVE

            GLib.idle_add(done)

        threading.Thread(target=work, daemon=True, name='ready2rip-eject').start()


    def _do_auto_eject(self) -> bool:
        if not self.store.get().auto_eject:
            return GLib.SOURCE_REMOVE
        # Don't fight a brand-new rip / cancel path.
        if self._ripping:
            return GLib.SOURCE_REMOVE
        self._eject_disc()
        return GLib.SOURCE_REMOVE
