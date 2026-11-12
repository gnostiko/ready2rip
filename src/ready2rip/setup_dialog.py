# SPDX-License-Identifier: GPL-3.0-or-later
"""First-run / re-run drive offset detection dialog."""

from __future__ import annotations

import threading

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gdk, GLib, Gtk, Pango

from ready2rip.drive_setup import (
    CalibrationResult,
    calibrate_drive_offset,
    save_calibration,
)
from ready2rip.settings import SettingsStore

_EASE = 'cubic-bezier(0.33, 1, 0.68, 1)'

_SETUP_CSS = f"""
.ready2rip-setup-flash {{
    background-color: @success_bg_color;
    opacity: 0;
    transition: opacity 200ms {_EASE};
}}
.ready2rip-setup-flash.error {{
    background-color: @error_bg_color;
}}
.ready2rip-setup-flash.visible {{
    opacity: 0.34;
}}
.ready2rip-setup-page {{
    padding: 8px 20px 16px 20px;
}}
"""

_setup_css_provider: Gtk.CssProvider | None = None


class DriveSetupDialog(Adw.Dialog):
    """Compact Adwaita dialog; detecting swaps to a status + Cancel page."""

    def __init__(self, store: SettingsStore, **kwargs) -> None:
        super().__init__(**kwargs)
        self.set_title('Detect sample offset')
        self.set_content_width(400)
        self.set_content_height(340)
        self._store = store
        self._busy = False
        self._cancel = threading.Event()
        self._flash_id = None
        self._leave_progress_id = None

        settings = store.get()
        self._install_styles()

        root = Gtk.Overlay(hexpand=True, vexpand=True)

        self._flash_veil = Gtk.Box()
        self._flash_veil.set_hexpand(True)
        self._flash_veil.set_vexpand(True)
        self._flash_veil.set_can_target(False)
        self._flash_veil.add_css_class('ready2rip-setup-flash')

        toolbar = Adw.ToolbarView()

        self._stack = Gtk.Stack(vexpand=True, hexpand=True)
        self._stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._stack.set_transition_duration(200)

        intro = self._page()
        intro.append(self._heading('Detect sample offset'))
        intro.append(
            self._body(
                'Insert a commercial audio CD. ready2rip measures the '
                'AccurateRip sample offset for this drive.'
            )
        )

        device_group = Adw.PreferencesGroup()
        self._device_row = Adw.EntryRow(title='Optical drive')
        self._device_row.set_text(settings.device or '/dev/sr0')
        device_group.add(self._device_row)
        intro.append(device_group)

        self._start_btn = Gtk.Button(label='Start')
        self._start_btn.add_css_class('pill')
        self._start_btn.add_css_class('suggested-action')
        self._start_btn.set_halign(Gtk.Align.CENTER)
        self._start_btn.set_margin_top(8)
        self._start_btn.connect('clicked', self._on_start)
        intro.append(self._start_btn)
        self._stack.add_named(intro, 'intro')

        progress = self._page()
        self._prog_title = self._heading('Detecting…')
        progress.append(self._prog_title)

        self._prog_status = Gtk.Label(label='Starting')
        self._prog_status.add_css_class('body')
        self._prog_status.add_css_class('dim-label')
        self._prog_status.set_halign(Gtk.Align.CENTER)
        self._prog_status.set_justify(Gtk.Justification.CENTER)
        self._prog_status.set_wrap(True)
        self._prog_status.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self._prog_status.set_max_width_chars(36)
        self._prog_status.set_ellipsize(Pango.EllipsizeMode.END)
        self._prog_status.set_selectable(False)
        progress.append(self._prog_status)

        self._cancel_btn = Gtk.Button(label='Cancel')
        self._cancel_btn.add_css_class('pill')
        self._cancel_btn.set_halign(Gtk.Align.CENTER)
        self._cancel_btn.set_margin_top(12)
        self._cancel_btn.connect('clicked', self._on_cancel)
        progress.append(self._cancel_btn)
        self._stack.add_named(progress, 'progress')

        result = self._page()
        self._result_title = self._heading('Drive ready')
        result.append(self._result_title)
        self._result_desc = self._body('')
        self._result_desc.set_selectable(True)
        result.append(self._result_desc)

        done_btn = Gtk.Button(label='Done')
        done_btn.add_css_class('pill')
        done_btn.add_css_class('suggested-action')
        done_btn.set_halign(Gtk.Align.CENTER)
        done_btn.set_margin_top(4)
        done_btn.connect('clicked', lambda *_: self.close())
        result.append(done_btn)

        self._retry_btn = Gtk.Button(label='Try again')
        self._retry_btn.add_css_class('flat')
        self._retry_btn.set_halign(Gtk.Align.CENTER)
        self._retry_btn.connect('clicked', self._on_retry)
        result.append(self._retry_btn)

        self._manual_btn = Gtk.Button(label='Enter offset manually')
        self._manual_btn.add_css_class('flat')
        self._manual_btn.set_halign(Gtk.Align.CENTER)
        self._manual_btn.connect('clicked', self._on_manual)
        result.append(self._manual_btn)
        self._stack.add_named(result, 'result')

        manual = self._page()
        manual.append(self._heading('Sample offset'))
        manual.append(
            self._body(
                'Enter the AccurateRip value for this drive. '
                'See accuraterip.com/driveoffsets.htm.'
            )
        )
        offset_group = Adw.PreferencesGroup()
        self._manual_offset = Adw.SpinRow(
            title='Sample offset',
            adjustment=Gtk.Adjustment(
                value=settings.drive_sample_offset,
                lower=-2000,
                upper=2000,
                step_increment=1,
                page_increment=10,
            ),
            digits=0,
        )
        offset_group.add(self._manual_offset)
        manual.append(offset_group)

        save_manual = Gtk.Button(label='Save')
        save_manual.add_css_class('pill')
        save_manual.add_css_class('suggested-action')
        save_manual.set_halign(Gtk.Align.CENTER)
        save_manual.set_margin_top(4)
        save_manual.connect('clicked', self._on_save_manual)
        manual.append(save_manual)
        self._stack.add_named(manual, 'manual')

        toolbar.set_content(self._stack)
        root.set_child(toolbar)
        root.add_overlay(self._flash_veil)
        self.set_child(root)
        self._stack.set_visible_child_name('intro')
        self.connect('closed', self._on_closed)

    def _install_styles(self) -> None:
        global _setup_css_provider
        display = self.get_display() or Gdk.Display.get_default()
        if display is None:
            return
        if _setup_css_provider is not None:
            return
        provider = Gtk.CssProvider()
        provider.load_from_string(_SETUP_CSS)
        Gtk.StyleContext.add_provider_for_display(
            display,
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )
        _setup_css_provider = provider

    @staticmethod
    def _page() -> Gtk.Box:
        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=10,
            valign=Gtk.Align.CENTER,
            hexpand=True,
            vexpand=True,
        )
        box.add_css_class('ready2rip-setup-page')
        return box

    @staticmethod
    def _heading(text: str) -> Gtk.Label:
        label = Gtk.Label(label=text)
        label.add_css_class('title-2')
        label.set_halign(Gtk.Align.CENTER)
        label.set_wrap(True)
        label.set_justify(Gtk.Justification.CENTER)
        label.set_max_width_chars(32)
        return label

    @staticmethod
    def _body(text: str) -> Gtk.Label:
        label = Gtk.Label(
            label=text,
            wrap=True,
            justify=Gtk.Justification.CENTER,
            max_width_chars=36,
        )
        label.add_css_class('body')
        label.add_css_class('dim-label')
        label.set_halign(Gtk.Align.CENTER)
        return label

    def _device(self) -> str:
        return self._device_row.get_text().strip() or '/dev/sr0'

    def _on_closed(self, *_args) -> None:
        self._cancel.set()
        self._cancel_leave_progress()

    def _cancel_leave_progress(self) -> None:
        if self._leave_progress_id is not None:
            GLib.source_remove(self._leave_progress_id)
            self._leave_progress_id = None

    def _leave_progress(self, page: str, *, delay_ms: int = 0) -> None:
        """Swap away from the detecting page."""
        self._cancel_leave_progress()

        def _go() -> bool:
            self._leave_progress_id = None
            self._stack.set_visible_child_name(page)
            return GLib.SOURCE_REMOVE

        if delay_ms <= 0:
            _go()
            return
        self._leave_progress_id = GLib.timeout_add(delay_ms, _go)

    def _on_cancel(self, *_args) -> None:
        if not self._busy:
            self._leave_progress('intro')
            return
        self._cancel.set()
        self._prog_title.set_label('Cancelling…')
        self._prog_status.set_label('Stopping detection')

    def _on_retry(self, *_args) -> None:
        self._leave_progress('intro')

    def _on_start(self, *_args) -> None:
        if self._busy:
            return
        device = self._device()
        self._busy = True
        self._cancel.clear()
        self._cancel_leave_progress()
        self._prog_title.set_label('Detecting…')
        self._prog_status.set_label('Starting')
        self._start_btn.set_sensitive(False)
        self._device_row.set_sensitive(False)
        self._cancel_btn.set_sensitive(True)
        self._stack.set_visible_child_name('progress')

        def worker() -> None:
            def on_prog(msg: str, frac: float) -> None:
                GLib.idle_add(self._update_progress, msg, frac)

            result = calibrate_drive_offset(
                device,
                on_progress=on_prog,
                should_cancel=self._cancel.is_set,
            )

            def done() -> bool:
                self._busy = False
                self._start_btn.set_sensitive(True)
                self._device_row.set_sensitive(True)
                if self._cancel.is_set() and not result.success:
                    self._show_cancelled()
                else:
                    self._show_result(device, result)
                return GLib.SOURCE_REMOVE

            GLib.idle_add(done)

        threading.Thread(target=worker, daemon=True).start()

    def _update_progress(self, msg: str, _frac: float = 0.0) -> bool:
        self._prog_status.set_label(msg)
        lower = msg.casefold()
        if 'cancel' in lower:
            self._prog_title.set_label('Cancelling…')
        elif 'extract' in lower or 'reading track' in lower:
            self._prog_title.set_label('Reading track')
        elif 'offset' in lower or 'scanning' in lower or 'checking' in lower:
            self._prog_title.set_label('Matching offsets')
        elif 'database' in lower or 'looking up' in lower:
            self._prog_title.set_label('Looking up disc')
        elif 'table of contents' in lower or 'reading disc' in lower:
            self._prog_title.set_label('Reading disc')
        elif 'match found' in lower:
            self._prog_title.set_label('Sample offset detected')
        else:
            self._prog_title.set_label('Detecting…')
        return GLib.SOURCE_REMOVE

    def _flash(self, kind: str = 'success') -> None:
        if self._flash_id is not None:
            GLib.source_remove(self._flash_id)
            self._flash_id = None
        if kind == 'error':
            self._flash_veil.add_css_class('error')
        else:
            self._flash_veil.remove_css_class('error')
        self._flash_veil.add_css_class('visible')
        self._flash_id = GLib.timeout_add(350, self._hide_flash)

    def _hide_flash(self) -> bool:
        self._flash_id = None
        self._flash_veil.remove_css_class('visible')
        GLib.timeout_add(220, self._clear_flash_error)
        return GLib.SOURCE_REMOVE

    def _clear_flash_error(self) -> bool:
        if 'visible' not in self._flash_veil.get_css_classes():
            self._flash_veil.remove_css_class('error')
        return GLib.SOURCE_REMOVE

    def _show_cancelled(self) -> None:
        self._result_title.set_label('Detection cancelled')
        self._result_desc.set_label(
            'No offset was saved. Start again when a commercial CD is ready.'
        )
        self._retry_btn.set_visible(True)
        self._manual_btn.set_visible(True)
        self._leave_progress('result')

    def _show_result(self, device: str, result: CalibrationResult) -> None:
        if result.success and result.offset is not None:
            save_calibration(self._store, device=device, offset=result.offset)
            self._prog_title.set_label('Sample offset detected')
            self._prog_status.set_label(f'{result.offset:+d} samples')
            self._flash('success')
            self._result_title.set_label('Sample offset detected')
            conf = (
                f' · confidence {result.confidence}'
                if result.confidence
                else ''
            )
            track = (
                f' · track {result.track_number}'
                if result.track_number
                else ''
            )
            self._result_desc.set_label(
                f'{result.offset:+d} samples{conf}{track}\nSaved for {device}.'
            )
            self._retry_btn.set_visible(False)
            self._manual_btn.set_visible(False)
            self._leave_progress('result', delay_ms=450)
            return

        self._flash('error')
        self._result_title.set_label('Could not detect offset')
        detail = (result.message or '').strip() or (
            'No AccurateRip match for this disc and drive.'
        )
        self._result_desc.set_label(
            f'{detail}\nTry another commercial CD, or enter the offset manually.'
        )
        self._retry_btn.set_visible(True)
        self._manual_btn.set_visible(True)
        self._leave_progress('result', delay_ms=350)

    def _on_manual(self, *_args) -> None:
        self._stack.set_visible_child_name('manual')

    def _on_save_manual(self, *_args) -> None:
        device = self._device()
        offset = int(self._manual_offset.get_value())
        save_calibration(self._store, device=device, offset=offset)
        self._result_title.set_label('Offset saved')
        self._result_desc.set_label(f'Saved {offset:+d} samples for {device}.')
        self._retry_btn.set_visible(False)
        self._manual_btn.set_visible(False)
        self._stack.set_visible_child_name('result')
