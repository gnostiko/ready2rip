# SPDX-License-Identifier: GPL-3.0-or-later
"""First-run / re-run drive offset + cache setup dialog (compact)."""

from __future__ import annotations

import threading

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, GLib, Gtk  # noqa: E402

from ready2rip.drive_setup import (  # noqa: E402
    CalibrationResult,
    calibrate_drive_offset,
    save_calibration,
)
from ready2rip.settings import SettingsStore  # noqa: E402


class DriveSetupDialog(Adw.Dialog):
    """Calibrate AccurateRip offset and analyze drive features (compact UI)."""

    def __init__(self, store: SettingsStore, **kwargs) -> None:
        super().__init__(**kwargs)
        self.set_title('Drive setup')
        # Compact fixed dialog — content is designed to fit without scrolling.
        self.set_content_width(420)
        self.set_content_height(380)
        self._store = store
        self._finished_ok = False
        self._busy = False
        self._last_result: CalibrationResult | None = None

        settings = store.get()

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        toolbar.add_top_bar(header)

        self._stack = Gtk.Stack(vexpand=True, hexpand=True)
        self._stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)

        # —— Intro (compact; no full StatusPage) ——
        intro = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=10,
            margin_start=16,
            margin_end=16,
            margin_top=8,
            margin_bottom=16,
            valign=Gtk.Align.CENTER,
        )
        icon = Gtk.Image.new_from_icon_name('media-optical-symbolic')
        icon.set_pixel_size(48)
        icon.set_halign(Gtk.Align.CENTER)
        intro.append(icon)

        title = Gtk.Label(label='Calibrate CD drive')
        title.add_css_class('title-3')
        title.set_halign(Gtk.Align.CENTER)
        intro.append(title)

        desc = Gtk.Label(
            label=(
                'Measures sample offset, audio cache, Accurate Stream, '
                'and C2 pointers. Insert a commercial CD, then start.'
            ),
            wrap=True,
            justify=Gtk.Justification.CENTER,
            max_width_chars=42,
        )
        desc.add_css_class('body')
        desc.add_css_class('dim-label')
        desc.set_halign(Gtk.Align.CENTER)
        intro.append(desc)

        device_group = Adw.PreferencesGroup()
        self._device_row = Adw.EntryRow(title='Optical drive')
        self._device_row.set_text(settings.device or '/dev/sr0')
        device_group.add(self._device_row)
        intro.append(device_group)

        start_btn = Gtk.Button(label='Start calibration')
        start_btn.add_css_class('suggested-action')
        start_btn.add_css_class('pill')
        start_btn.set_halign(Gtk.Align.CENTER)
        start_btn.set_margin_top(4)
        start_btn.connect('clicked', self._on_start)
        intro.append(start_btn)

        skip_btn = Gtk.Button(label='Skip (offset 0)')
        skip_btn.add_css_class('flat')
        skip_btn.set_halign(Gtk.Align.CENTER)
        skip_btn.connect('clicked', self._on_skip)
        intro.append(skip_btn)

        self._stack.add_named(intro, 'intro')

        # —— Progress ——
        prog_page = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            valign=Gtk.Align.CENTER,
            margin_start=20,
            margin_end=20,
        )
        self._prog_title = Gtk.Label(label='Calibrating…')
        self._prog_title.add_css_class('title-3')
        self._prog_title.set_halign(Gtk.Align.CENTER)
        self._prog_status = Gtk.Label(
            label='Starting…',
            wrap=True,
            justify=Gtk.Justification.CENTER,
            max_width_chars=40,
        )
        self._prog_status.add_css_class('body')
        self._prog_status.add_css_class('dim-label')
        self._prog_bar = Gtk.ProgressBar(show_text=True, hexpand=True)
        prog_page.append(self._prog_title)
        prog_page.append(self._prog_status)
        prog_page.append(self._prog_bar)
        self._stack.add_named(prog_page, 'progress')

        # —— Result (compact card, no huge StatusPage) ——
        result = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=10,
            margin_start=16,
            margin_end=16,
            margin_top=8,
            margin_bottom=16,
            valign=Gtk.Align.CENTER,
        )
        self._result_icon = Gtk.Image.new_from_icon_name('emblem-ok-symbolic')
        self._result_icon.set_pixel_size(40)
        self._result_icon.set_halign(Gtk.Align.CENTER)
        result.append(self._result_icon)

        self._result_title = Gtk.Label(label='Drive ready')
        self._result_title.add_css_class('title-3')
        self._result_title.set_halign(Gtk.Align.CENTER)
        result.append(self._result_title)

        self._result_desc = Gtk.Label(
            label='',
            wrap=True,
            justify=Gtk.Justification.CENTER,
            max_width_chars=44,
            selectable=True,
        )
        self._result_desc.add_css_class('body')
        self._result_desc.add_css_class('dim-label')
        self._result_desc.set_halign(Gtk.Align.CENTER)
        result.append(self._result_desc)

        done_btn = Gtk.Button(label='Continue')
        done_btn.add_css_class('suggested-action')
        done_btn.add_css_class('pill')
        done_btn.set_halign(Gtk.Align.CENTER)
        done_btn.set_margin_top(4)
        done_btn.connect('clicked', lambda *_: self.close())
        result.append(done_btn)

        self._manual_btn = Gtk.Button(label='Enter offset manually…')
        self._manual_btn.add_css_class('flat')
        self._manual_btn.set_halign(Gtk.Align.CENTER)
        self._manual_btn.connect('clicked', self._on_manual)
        result.append(self._manual_btn)

        self._stack.add_named(result, 'result')

        # —— Manual offset ——
        manual = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=10,
            margin_start=16,
            margin_end=16,
            margin_top=12,
            margin_bottom=16,
            valign=Gtk.Align.CENTER,
        )
        manual_lbl = Gtk.Label(
            label='Enter the AccurateRip sample offset for this drive.',
            wrap=True,
            justify=Gtk.Justification.CENTER,
            max_width_chars=40,
        )
        manual_lbl.add_css_class('body')
        manual_lbl.add_css_class('dim-label')
        manual.append(manual_lbl)

        offset_group = Adw.PreferencesGroup()
        self._manual_offset = Adw.SpinRow(
            title='Sample offset',
            subtitle='accuraterip.com/driveoffsets.htm',
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

        save_manual = Gtk.Button(label='Save offset')
        save_manual.add_css_class('suggested-action')
        save_manual.add_css_class('pill')
        save_manual.set_halign(Gtk.Align.CENTER)
        save_manual.connect('clicked', self._on_save_manual)
        manual.append(save_manual)
        self._stack.add_named(manual, 'manual')

        toolbar.set_content(self._stack)
        self.set_child(toolbar)
        self._stack.set_visible_child_name('intro')

    @property
    def finished_ok(self) -> bool:
        return self._finished_ok

    def _on_start(self, *_args) -> None:
        if self._busy:
            return
        device = self._device_row.get_text().strip() or '/dev/sr0'
        self._busy = True
        self._stack.set_visible_child_name('progress')
        self._prog_title.set_label('Calibrating…')
        self._prog_bar.set_fraction(0.0)
        self._prog_status.set_label('Starting…')

        def worker() -> None:
            def on_prog(msg: str, frac: float) -> None:
                GLib.idle_add(self._update_progress, msg, frac)

            result = calibrate_drive_offset(device, on_progress=on_prog)

            def done() -> bool:
                self._busy = False
                self._last_result = result
                self._show_result(device, result)
                return GLib.SOURCE_REMOVE

            GLib.idle_add(done)

        threading.Thread(target=worker, daemon=True).start()

    def _update_progress(self, msg: str, frac: float) -> bool:
        self._prog_status.set_label(msg)
        self._prog_bar.set_fraction(frac)
        self._prog_bar.set_text(f'{int(frac * 100)}%')
        lower = msg.casefold()
        if 'c2' in lower:
            self._prog_title.set_label('Testing C2…')
        elif 'accurate stream' in lower:
            self._prog_title.set_label('Accurate Stream…')
        elif 'cache' in lower:
            self._prog_title.set_label('Drive cache…')
        elif 'offset' in lower or 'scanning' in lower:
            self._prog_title.set_label('Sample offset…')
        elif 'extract' in lower:
            self._prog_title.set_label('Reading track…')
        return GLib.SOURCE_REMOVE

    def _show_result(self, device: str, result: CalibrationResult) -> None:
        features = _format_feature_lines(result)

        if result.success and result.offset is not None:
            _save_result(self._store, device, result, offset=result.offset)
            self._finished_ok = True
            self._result_icon.set_from_icon_name('emblem-ok-symbolic')
            self._result_title.set_label('Drive calibrated')
            body = result.message
            if features:
                body = f'{body}\n{features}'
            body = f'{body}\nSaved for {device}.'
            self._result_desc.set_label(body)
            self._manual_btn.set_visible(False)
        else:
            if (
                result.caches_audio is not None
                or result.accurate_stream is not None
                or result.c2_pointers is not None
            ):
                _save_result(
                    self._store,
                    device,
                    result,
                    offset=self._store.get().drive_sample_offset,
                )
                if not result.success:
                    self._store.update(drive_offset_configured=False)
                    try:
                        from gi.repository import Gio

                        Gio.Settings.sync()
                    except Exception:  # noqa: BLE001
                        pass

            self._result_icon.set_from_icon_name('dialog-warning-symbolic')
            self._result_title.set_label('Calibration incomplete')
            parts = [result.message or 'Calibration failed.']
            if features:
                parts.append(features)
            parts.append('Enter an offset manually or skip.')
            self._result_desc.set_label('\n'.join(parts))
            self._manual_btn.set_visible(True)
        self._stack.set_visible_child_name('result')

    def _on_skip(self, *_args) -> None:
        device = self._device_row.get_text().strip() or '/dev/sr0'
        save_calibration(self._store, device=device, offset=0)
        self._finished_ok = True
        self._result_icon.set_from_icon_name('emblem-ok-symbolic')
        self._result_title.set_label('Setup skipped')
        self._result_desc.set_label(
            f'Saved offset 0 for {device}. Re-run setup later for AccurateRip accuracy.'
        )
        self._manual_btn.set_visible(True)
        self._stack.set_visible_child_name('result')

    def _on_manual(self, *_args) -> None:
        self._stack.set_visible_child_name('manual')

    def _on_save_manual(self, *_args) -> None:
        device = self._device_row.get_text().strip() or '/dev/sr0'
        offset = int(self._manual_offset.get_value())
        prev = self._last_result
        if prev is not None:
            _save_result(self._store, device, prev, offset=offset)
            features = _format_feature_lines(prev)
        else:
            s = self._store.get()
            save_calibration(
                self._store,
                device=device,
                offset=offset,
                caches_audio=(
                    s.drive_caches_audio if s.drive_cache_configured else None
                ),
                cache_message=s.drive_cache_message,
                accurate_stream=(
                    s.drive_accurate_stream
                    if s.drive_accurate_stream_configured
                    else None
                ),
                accurate_stream_message=s.drive_accurate_stream_message,
                c2_pointers=(
                    s.drive_c2_pointers if s.drive_c2_configured else None
                ),
                c2_message=s.drive_c2_message,
            )
            features = ''
        self._finished_ok = True
        self._result_icon.set_from_icon_name('emblem-ok-symbolic')
        self._result_title.set_label('Offset saved')
        body = f'Saved sample offset {offset} for {device}.'
        if features:
            body = f'{body}\n{features}'
        self._result_desc.set_label(body)
        self._manual_btn.set_visible(False)
        self._stack.set_visible_child_name('result')


def _save_result(
    store,
    device: str,
    result: CalibrationResult,
    *,
    offset: int,
) -> None:
    save_calibration(
        store,
        device=device,
        offset=offset,
        caches_audio=result.caches_audio,
        cache_message=result.cache_message,
        accurate_stream=result.accurate_stream,
        accurate_stream_message=result.accurate_stream_message,
        c2_pointers=result.c2_pointers,
        c2_message=result.c2_message,
    )


def _format_feature_lines(result: CalibrationResult) -> str:
    """One short line per feature for a compact result summary."""
    lines: list[str] = []

    if result.c2_pointers is True:
        lines.append('C2 pointers: yes')
    elif result.c2_pointers is False:
        lines.append('C2 pointers: no')
    elif result.c2_message:
        lines.append('C2 pointers: unknown')

    if result.accurate_stream is True:
        lines.append('Accurate Stream: yes')
    elif result.accurate_stream is False:
        lines.append('Accurate Stream: no')
    elif result.accurate_stream_message:
        lines.append('Accurate Stream: unknown')

    if result.caches_audio is True:
        lines.append('Audio cache: yes (defeat on)')
    elif result.caches_audio is False:
        lines.append('Audio cache: no')
    elif result.cache_message:
        lines.append('Audio cache: unknown')

    return '\n'.join(lines)
