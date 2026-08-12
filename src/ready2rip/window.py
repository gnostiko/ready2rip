# SPDX-License-Identifier: GPL-3.0-or-later
"""Main application window."""

from __future__ import annotations

import threading
from pathlib import Path

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gio, GLib, Gtk, Pango  # noqa: E402

from ready2rip.artwork.fetch import (  # noqa: E402
    ArtworkFetcher,
    ArtworkImage,
    ArtworkSourceOptions,
    apply_artwork_to_picture,
)
from ready2rip.disc.discid_util import DiscIdentifiers, identifiers_from_disc  # noqa: E402
from ready2rip.disc.drive_info import DriveInfo, probe_drive  # noqa: E402
from ready2rip.disc.drive_status import (  # noqa: E402
    DriveStatus,
    DriveTrayState,
    eject_drive,
    query_drive_status,
)
from ready2rip.disc.probe import DiscInfo, probe_disc  # noqa: E402
from ready2rip.metadata.cache import MetadataCache  # noqa: E402
from ready2rip.metadata.providers import (  # noqa: E402
    AlbumMetadata,
    TrackMetadata,
    fetch_album_from_musicbrainz_link,
    lookup_metadata,
    parse_musicbrainz_release_id,
)
from ready2rip.metadata_dialog import MetadataPickerDialog  # noqa: E402
from ready2rip.paths import track_meta_for  # noqa: E402
from ready2rip.rip.engine import RipEngine, RipJob, RipProgress, RipResult, RipState  # noqa: E402
from ready2rip.settings import (  # noqa: E402
    ARTWORK_SIZES,
    ENCODERS,
    SettingsStore,
    default_output_directory,
)
from ready2rip.theme import (  # noqa: E402
    COLOR_SCHEME_ICONS,
    COLOR_SCHEME_LABELS,
    COLOR_SCHEMES,
    ChromeStyleController,
    apply_color_scheme,
    next_color_scheme,
    normalize_color_scheme,
)
from ready2rip.window_styles import install_window_styles  # noqa: E402


_WINDOW_UI = str(Path(__file__).with_name('window.ui'))


@Gtk.Template(filename=_WINDOW_UI)
class Ready2RipWindow(Adw.ApplicationWindow):
    __gtype_name__ = 'Ready2RipWindow'

    toast_overlay = Gtk.Template.Child()
    toolbar_view = Gtk.Template.Child()
    header_bar = Gtk.Template.Child()
    theme_button = Gtk.Template.Child()
    breakpoint_bin = Gtk.Template.Child()
    split_view = Gtk.Template.Child()
    sidebar_scroll = Gtk.Template.Child()
    sidebar_box = Gtk.Template.Child()
    sidebar_button = Gtk.Template.Child()
    status_page = Gtk.Template.Child()
    stack = Gtk.Template.Child()
    track_edit_group = Gtk.Template.Child()
    album_fields_box = Gtk.Template.Child()
    album_edit_group = Gtk.Template.Child()
    disc_group = Gtk.Template.Child()
    drive_group = Gtk.Template.Child()
    options_group = Gtk.Template.Child()
    metadata_group = Gtk.Template.Child()
    rip_button = Gtk.Template.Child()
    lookup_button = Gtk.Template.Child()
    eject_button = Gtk.Template.Child()
    metadata_origin_label = Gtk.Template.Child()
    art_size_label = Gtk.Template.Child()
    art_origin_label = Gtk.Template.Child()
    cover_frame = Gtk.Template.Child()
    cover_overlay = Gtk.Template.Child()
    cover_picture = Gtk.Template.Child()
    cover_placeholder = Gtk.Template.Child()
    cover_actions = Gtk.Template.Child()
    search_art_button = Gtk.Template.Child()
    choose_art_button = Gtk.Template.Child()
    clear_art_button = Gtk.Template.Child()
    lookup_progress = Gtk.Template.Child()
    rip_revealer = Gtk.Template.Child()
    rip_banner = Gtk.Template.Child()
    rip_title_label = Gtk.Template.Child()
    rip_status_label = Gtk.Template.Child()
    rip_progress = Gtk.Template.Child()
    rip_percent_label = Gtk.Template.Child()

    # Cover above full-width album fields (matches Tracks column span).
    _COVER_SIZE = 240
    _COVER_PLACEHOLDER_ICON_SIZE = 72

    def __init__(self, store: SettingsStore | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self._disc: DiscInfo | None = None
        self._ids: DiscIdentifiers | None = None
        self._album: AlbumMetadata | None = None
        self._track_rows: list[Gtk.Widget] = []
        self._track_checks: dict[int, Gtk.CheckButton] = {}
        self._track_title_entries: dict[int, Gtk.Entry] = {}
        self._track_artist_entries: dict[int, Gtk.Entry] = {}
        self._track_title_labels: dict[int, Gtk.Label] = {}
        self._track_artist_labels: dict[int, Gtk.Label] = {}
        self._track_status_labels: dict[int, Gtk.Label] = {}
        self._track_title_stacks: dict[int, Gtk.Stack] = {}
        self._track_artist_stacks: dict[int, Gtk.Stack] = {}
        self._tracks_editing = False
        self._track_edit_button: Gtk.Button | None = None
        self._track_fill_button: Gtk.Button | None = None
        # Align title / artist columns across all track rows.
        self._track_title_size = Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL)
        self._track_artist_size = Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL)
        self._album_field_rows: list[Gtk.Widget] = []
        self._album_title_row: Adw.EntryRow | None = None
        self._album_artist_row: Adw.EntryRow | None = None
        self._album_date_row: Adw.EntryRow | None = None
        self._album_label_row: Adw.EntryRow | None = None
        self._album_disc_row: Adw.EntryRow | None = None
        self._disc_rows: list[Adw.ActionRow] = []
        self._drive_rows: list[Gtk.Widget] = []
        self._option_rows: list[Gtk.Widget] = []
        self._metadata_rows: list[Gtk.Widget] = []
        self._lookup_generation = 0
        self._looking_up = False
        self._artwork: ArtworkImage | None = None
        self._artwork_embed: ArtworkImage | None = None
        self._art_generation = 0
        self._ripping = False
        self._rip_engine: RipEngine | None = None
        self._suppress_meta_write = False
        self._meta_cache = MetadataCache()
        self._cache_save_id: int | None = None
        self._syncing_sidebar = False
        self._progress_hide_id: int | None = None
        self._drive_status: DriveStatus | None = None
        self._last_tray_state: DriveTrayState | None = None
        self._drive_info: DriveInfo | None = None
        # Disc identity last auto-ripped / loaded (avoid re-ripping same media).
        self._auto_rip_disc_key: str | None = None
        self._auto_rip_pending = False
        self._poll_id: int | None = None
        # Drop stale background probe results when a newer probe starts.
        self._probe_generation = 0
        self._probe_in_flight = False
        self._poll_generation = 0

        self.store = store if store is not None else SettingsStore()
        self._device = self.store.get().device

        self._setup_lookup_menu()
        self._setup_theme_button()
        self._setup_split_view()
        self._setup_cover_widget()
        self._setup_progress_styles()
        self._setup_chrome_style()
        self._build_calibration_row()
        self._build_options_rows()
        self._build_metadata_rows()
        self._build_album_edit_rows()
        # Do not touch the optical drive during construction — cdparanoia / ioctl
        # can block for tens of seconds and makes AppImage / cold start feel stuck.
        # Defer probe + drive panel until the window is shown.
        self.status_page.set_description(
            'Starting… detecting disc and drive (this may take a moment).'
        )
        GLib.idle_add(self._deferred_startup)

    def _deferred_startup(self) -> bool:
        """Run after the first UI frame so the window appears immediately."""
        device = self.store.get().device or self._device or '/dev/sr0'
        self._device = device
        self.status_page.set_title('Looking for a disc')
        self.status_page.set_description(
            f'Looking for an audio CD on {device}…'
        )
        # Drive identity (udev/sysfs) is cheap — fill Technical → Drive immediately.
        try:
            self._rebuild_drive_rows(device, allow_optical=False)
        except Exception:  # noqa: BLE001
            pass
        self._start_async_probe(device, reason='startup')
        return GLib.SOURCE_REMOVE

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
            import logging

            log = logging.getLogger(__name__)
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
                except Exception as drive_exc:  # noqa: BLE001
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
            except Exception as exc:  # noqa: BLE001
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
                            f'{err or "Unknown error"}\n'
                            'Check Optical device path in Rip options.'
                        )
                        if drive is not None:
                            self._drive_info = drive
                            self._rebuild_drive_rows(
                                device, drive_info=drive, allow_optical=False
                            )
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
                except Exception:  # noqa: BLE001
                    log.exception('Disc probe UI update failed (%s)', reason)
                    if reason == 'startup':
                        self._start_drive_monitor()
                return GLib.SOURCE_REMOVE

            GLib.idle_add(finish)

        threading.Thread(target=work, daemon=True, name=f'ready2rip-probe-{reason}').start()

    def _setup_split_view(self) -> None:
        """GNOME-style collapsible sidebar; auto-collapses on narrow widths."""
        # Keep header toggle in sync when the user swipes the sidebar closed.
        self.split_view.connect(
            'notify::show-sidebar',
            self._on_show_sidebar_changed,
        )

        # Collapse to overlay below ~900sp so album/tracks keep room.
        try:
            condition = Adw.BreakpointCondition.parse('max-width: 900sp')
            breakpoint = Adw.Breakpoint.new(condition)
            breakpoint.add_setter(self.split_view, 'collapsed', True)
            self.breakpoint_bin.add_breakpoint(breakpoint)
        except (TypeError, AttributeError, GLib.Error):
            # Older libadwaita without Breakpoint API — leave side-by-side.
            pass

    @Gtk.Template.Callback()
    def _on_sidebar_toggled(self, button: Gtk.ToggleButton) -> None:
        if self._syncing_sidebar:
            return
        self.split_view.set_show_sidebar(button.get_active())

    def _on_show_sidebar_changed(self, *_args) -> None:
        if self._syncing_sidebar:
            return
        self._syncing_sidebar = True
        try:
            self.sidebar_button.set_active(self.split_view.get_show_sidebar())
        finally:
            self._syncing_sidebar = False

    def _setup_cover_widget(self) -> None:
        """Constrain cover to a fixed square; show action buttons on hover."""
        size = self._COVER_SIZE
        for widget in (self.cover_frame, self.cover_overlay, self.cover_picture):
            widget.set_size_request(size, size)
            widget.set_hexpand(True)
            widget.set_vexpand(True)

        # Gtk.Picture is the correct widget for full-bleed album art.
        self.cover_picture.set_content_fit(Gtk.ContentFit.COVER)
        if hasattr(self.cover_picture, 'set_can_shrink'):
            self.cover_picture.set_can_shrink(True)
        # Frame must not grow to the paintable's natural size.
        self.cover_frame.set_hexpand(False)
        self.cover_frame.set_vexpand(False)
        self.cover_frame.set_halign(Gtk.Align.CENTER)

        self.cover_actions.set_opacity(0.0)
        for button in (
            self.search_art_button,
            self.choose_art_button,
            self.clear_art_button,
        ):
            button.connect(
                'notify::has-focus',
                self._on_cover_button_focus_changed,
            )
        self._show_placeholder_cover()
        self._sync_cover_action_sensitivity()

        motion = Gtk.EventControllerMotion()
        motion.connect('enter', self._on_cover_pointer_enter)
        motion.connect('leave', self._on_cover_pointer_leave)
        self.cover_frame.add_controller(motion)

    def _setup_progress_styles(self) -> None:
        """Install cover / track / progress CSS."""
        install_window_styles(self.get_display(), cover_size=self._COVER_SIZE)

    def _setup_chrome_style(self) -> None:
        """Flat custom chrome: pure black dark, warm grey light."""
        self._chrome = ChromeStyleController(self)

    def _set_cover_actions_visible(self, visible: bool) -> None:
        # Opacity only — leave can_target enabled so moving onto the buttons
        # does not race with leave/hide before click.
        self.cover_actions.set_opacity(1.0 if visible else 0.0)

    def _cover_action_has_focus(self) -> bool:
        return (
            self.search_art_button.has_focus()
            or self.choose_art_button.has_focus()
            or self.clear_art_button.has_focus()
        )

    def _on_cover_pointer_enter(self, *_args) -> None:
        self._set_cover_actions_visible(True)

    def _on_cover_pointer_leave(self, *_args) -> None:
        if not self._cover_action_has_focus():
            self._set_cover_actions_visible(False)

    def _on_cover_button_focus_changed(self, button: Gtk.Button, *_args) -> None:
        # Keyboard focus: show while focused, hide when focus leaves (unless hovered).
        if self._cover_action_has_focus():
            self._set_cover_actions_visible(True)
        else:
            self._set_cover_actions_visible(False)

    def _sync_cover_action_sensitivity(self) -> None:
        self.clear_art_button.set_sensitive(self._artwork is not None)

    def _show_placeholder_cover(self) -> None:
        """Clear picture and show a compact music icon (visible in light/dark)."""
        size = self._COVER_SIZE
        self.cover_picture.set_paintable(None)
        self.cover_picture.set_size_request(size, size)
        self.cover_overlay.set_size_request(size, size)
        self.cover_frame.set_size_request(size, size)

        icon_px = self._COVER_PLACEHOLDER_ICON_SIZE
        self.cover_placeholder.set_pixel_size(icon_px)
        # Prefer a widely available symbolic glyph; fall back if a theme omits one.
        theme = Gtk.IconTheme.get_for_display(self.get_display())
        for name in (
            'folder-music-symbolic',
            'audio-x-generic-symbolic',
            'media-optical-symbolic',
            'emblem-music-symbolic',
        ):
            if theme.has_icon(name):
                self.cover_placeholder.set_from_icon_name(name)
                break
        else:
            self.cover_placeholder.set_from_icon_name('folder-music-symbolic')
        self.cover_placeholder.add_css_class('ready2rip-cover-placeholder')
        self.cover_placeholder.set_visible(True)
        # Ensure the icon paints above an empty GtkPicture in all themes.
        self.cover_placeholder.set_opacity(1.0)

    # —— Sidebar groups ——

    def _build_calibration_row(self) -> None:
        """Calibrate drive row at the top of the Ripping group."""
        self._setup_row = Adw.ActionRow(
            title='Calibrate drive',
            activatable=True,
        )
        # Status checkmark when calibrated (GNOME: symbolic + success).
        self._setup_ok_icon = Gtk.Image.new_from_icon_name('emblem-ok-symbolic')
        self._setup_ok_icon.set_valign(Gtk.Align.CENTER)
        self._setup_ok_icon.add_css_class('success')
        self._setup_ok_icon.set_visible(False)
        self._setup_row.add_suffix(self._setup_ok_icon)

        # Action: text button, or compact grey optical icon when complete.
        self._setup_btn = Gtk.Button(label='Calibrate')
        self._setup_btn.set_valign(Gtk.Align.CENTER)
        self._setup_btn.connect('clicked', self._on_run_drive_setup)
        self._setup_row.add_suffix(self._setup_btn)
        self._setup_row.set_activatable_widget(self._setup_btn)
        # First child of Ripping (built before other option rows).
        self.options_group.add(self._setup_row)
        self._update_calibration_row()

    def _update_calibration_row(self) -> None:
        """Refresh calibration row: check + compact grey icon button when done."""
        settings = self.store.get()
        done = bool(settings.drive_offset_configured)
        btn = self._setup_btn
        row = self._setup_row

        for cls in ('suggested-action', 'flat', 'circular', 'ready2rip-setup-icon'):
            btn.remove_css_class(cls)

        if done:
            device = settings.drive_offset_device or settings.device or 'this drive'
            offset = settings.drive_sample_offset
            row.set_title('Calibrated')
            row.set_subtitle(f'{offset:+d} samples · {device}')
            self._setup_ok_icon.set_visible(True)
            btn.set_icon_name('media-optical-symbolic')
            btn.add_css_class('flat')
            btn.add_css_class('circular')
            btn.add_css_class('ready2rip-setup-icon')
            btn.set_tooltip_text('Recalibrate drive')
        else:
            row.set_title('Calibrate drive')
            row.set_subtitle('Offset, cache, Accurate Stream')
            self._setup_ok_icon.set_visible(False)
            btn.set_icon_name('')
            btn.set_label('Calibrate')
            btn.add_css_class('suggested-action')
            btn.set_tooltip_text('Calibrate this drive')

    def _add_option_row(self, row: Gtk.Widget) -> None:
        self.options_group.add(row)
        self._option_rows.append(row)

    def _add_metadata_row(self, row: Gtk.Widget) -> None:
        self.metadata_group.add(row)
        self._metadata_rows.append(row)

    def _build_options_rows(self) -> None:
        for row in self._option_rows:
            self.options_group.remove(row)
        self._option_rows.clear()

        settings = self.store.get()
        default_out = default_output_directory()

        # —— Drive (path + offset first) ——
        self._device_row = Adw.EntryRow(title='Device')
        self._device_row.set_text(settings.device or '/dev/sr0')
        self._device_row.set_tooltip_text('Optical device, e.g. /dev/sr0')
        self._device_row.connect('changed', self._on_device_path_changed)
        self._add_option_row(self._device_row)

        status = (
            f'{settings.drive_offset_device or "This drive"}'
            if settings.drive_offset_configured
            else 'Not calibrated'
        )
        self._offset_row = Adw.SpinRow(
            title='Sample offset',
            subtitle=status,
            adjustment=Gtk.Adjustment(
                value=settings.drive_sample_offset,
                lower=-2000,
                upper=2000,
                step_increment=1,
                page_increment=10,
            ),
            digits=0,
        )
        self._offset_row.connect('changed', self._on_offset_changed)
        self._add_option_row(self._offset_row)

        # —— Paths & templates ——
        self._output_row = Adw.EntryRow(title='Output')
        self._output_row.set_text(settings.output_directory or default_out)
        self._output_row.set_tooltip_text(f'Default: {default_out}')
        self._output_row.connect('changed', self._on_output_changed)
        self._add_option_row(self._output_row)

        self._folder_template_row = Adw.EntryRow(title='Album folder')
        self._folder_template_row.set_text(settings.album_folder_template)
        self._folder_template_row.set_tooltip_text(
            '{album_artist}/{album}/{disc_folder} · {year}, {disc}, {totaldiscs}'
        )
        self._folder_template_row.connect(
            'changed', self._on_folder_template_changed
        )
        self._add_option_row(self._folder_template_row)

        self._filename_row = Adw.EntryRow(title='Track filename')
        self._filename_row.set_text(settings.filename_template)
        self._filename_row.set_tooltip_text(
            '{track:02d} - {title} · {artist}, {album}, {disc}, {totaldiscs}'
        )
        self._filename_row.connect('changed', self._on_filename_changed)
        self._add_option_row(self._filename_row)

        # —— Encoder + quality ——
        self._encoder_row = Adw.ComboRow(title='Encoder')
        self._encoder_row.set_model(
            Gtk.StringList.new([label for _id, label in ENCODERS])
        )
        ids = [e[0] for e in ENCODERS]
        try:
            self._encoder_row.set_selected(ids.index(settings.encode_format))
        except ValueError:
            self._encoder_row.set_selected(0)
        self._encoder_row.connect('notify::selected', self._on_encoder_changed)
        self._add_option_row(self._encoder_row)

        self._encoder_quality_row = Adw.ExpanderRow(
            title='Encoder settings',
            subtitle='Quality for the selected format',
        )
        self._encoder_quality_row.set_expanded(False)

        self._flac_row = Adw.SpinRow(
            title='FLAC compression',
            subtitle='0 = fastest, 8 = smallest',
            adjustment=Gtk.Adjustment(
                value=settings.flac_compression,
                lower=0,
                upper=8,
                step_increment=1,
                page_increment=1,
            ),
            digits=0,
        )
        self._flac_row.connect('changed', self._on_flac_changed)
        self._encoder_quality_row.add_row(self._flac_row)

        self._mp3_row = Adw.ComboRow(title='MP3 bitrate')
        self._mp3_values = [128, 192, 256, 320]
        self._mp3_row.set_model(
            Gtk.StringList.new(
                [f'{r} kbps' for r in self._mp3_values]
            )
        )
        try:
            self._mp3_row.set_selected(
                self._mp3_values.index(settings.mp3_bitrate)
            )
        except ValueError:
            self._mp3_row.set_selected(3)
        self._mp3_row.connect('notify::selected', self._on_mp3_changed)
        self._encoder_quality_row.add_row(self._mp3_row)

        self._opus_row = Adw.SpinRow(
            title='Opus bitrate (kbps)',
            subtitle='Typical range 96–256',
            adjustment=Gtk.Adjustment(
                value=settings.opus_bitrate,
                lower=48,
                upper=512,
                step_increment=8,
                page_increment=32,
            ),
            digits=0,
        )
        self._opus_row.connect('changed', self._on_opus_changed)
        self._encoder_quality_row.add_row(self._opus_row)

        self._add_option_row(self._encoder_quality_row)
        self._update_quality_sensitivity(settings.encode_format)

        # —— Extraction toggles ——
        self._test_copy_row = Adw.SwitchRow(
            title='Test and copy',
            subtitle='Rip twice; require matching CRCs',
            active=settings.test_and_copy,
        )
        self._test_copy_row.connect('notify::active', self._on_test_copy_toggled)
        self._add_option_row(self._test_copy_row)

        self._copy_image_row = Adw.SwitchRow(
            title='Copy Image',
            subtitle=(
                'Rip one continuous disc image instead of separate '
                'track files (use Write .cue file for a matching sheet)'
            ),
            active=settings.copy_image,
        )
        self._copy_image_row.connect('notify::active', self._on_copy_image_toggled)
        self._add_option_row(self._copy_image_row)

        self._htoa_row = Adw.SwitchRow(
            title='Pregap / HTOA',
            subtitle=(
                'Extract non-silent audio before track 1 as 00; '
                'ignore the standard 2s pause; track 1 starts at index 01'
            ),
            active=settings.rip_htoa,
        )
        self._htoa_row.connect('notify::active', self._on_htoa_toggled)
        self._add_option_row(self._htoa_row)

        self._ar_row = Adw.SwitchRow(
            title='AccurateRip',
            subtitle='Verify against online CRC database',
            active=settings.verify_accuraterip,
        )
        self._ar_row.connect('notify::active', self._on_ar_toggled)
        self._add_option_row(self._ar_row)

        self._burst_row = Adw.SwitchRow(
            title='Burst fallback',
            subtitle='On CRC mismatch (after 1 retry), re-rip the whole CD with -Z',
            active=settings.burst_fallback,
        )
        self._burst_row.connect('notify::active', self._on_burst_toggled)
        self._add_option_row(self._burst_row)

        self._log_row = Adw.SwitchRow(
            title='Write rip log',
            subtitle='Detailed status log in album folder',
            active=settings.write_rip_log,
        )
        self._log_row.connect('notify::active', self._on_log_toggled)
        self._add_option_row(self._log_row)

        self._cue_row = Adw.SwitchRow(
            title='Write .cue file',
            subtitle=(
                'Multi-file CUE (left-out gaps) for secure rips '
                'or a single-image CUE when Copy Image is on'
            ),
            active=settings.write_cue_file,
        )
        self._cue_row.connect('notify::active', self._on_cue_toggled)
        self._add_option_row(self._cue_row)

        self._auto_rip_row = Adw.SwitchRow(
            title='Auto-rip',
            subtitle='Start ripping when a new audio CD is detected',
            active=settings.auto_rip,
        )
        self._auto_rip_row.connect('notify::active', self._on_auto_rip_toggled)
        self._add_option_row(self._auto_rip_row)

        self._auto_eject_row = Adw.SwitchRow(
            title='Auto-eject',
            subtitle='Open the tray after a successful rip',
            active=settings.auto_eject,
        )
        self._auto_eject_row.connect('notify::active', self._on_auto_eject_toggled)
        self._add_option_row(self._auto_eject_row)

    def _build_metadata_rows(self) -> None:
        for row in self._metadata_rows:
            self.metadata_group.remove(row)
        self._metadata_rows.clear()

        settings = self.store.get()

        # Look up automatically: expander with per-source toggles nested inside.
        self._auto_lookup_row = Adw.ExpanderRow(
            title='Look up automatically',
            subtitle='Query metadata when a disc is detected',
        )
        self._auto_lookup_row.set_show_enable_switch(True)
        self._auto_lookup_row.set_enable_expansion(settings.auto_lookup_metadata)
        self._auto_lookup_row.set_expanded(settings.auto_lookup_metadata)
        self._auto_lookup_row.connect(
            'notify::enable-expansion', self._on_auto_lookup_toggled
        )

        self._mb_row = Adw.SwitchRow(
            title='MusicBrainz',
            subtitle='Preferred source for album and track metadata',
            active=settings.use_musicbrainz,
        )
        self._mb_row.connect('notify::active', self._on_mb_toggled)
        self._auto_lookup_row.add_row(self._mb_row)

        self._freedb_row = Adw.SwitchRow(
            title='FreeDB / gnudb',
            subtitle='Fallback FreeDB-compatible lookup',
            active=settings.use_freedb,
        )
        self._freedb_row.connect('notify::active', self._on_freedb_toggled)
        self._auto_lookup_row.add_row(self._freedb_row)

        self._add_metadata_row(self._auto_lookup_row)
        self._sync_lookup_source_sensitivity(settings.auto_lookup_metadata)

        # Download artwork: sources, then embed options nested underneath.
        self._fetch_art_row = Adw.ExpanderRow(
            title='Download artwork',
            subtitle='Fetch covers and keep the highest quality match',
        )
        self._fetch_art_row.set_show_enable_switch(True)
        self._fetch_art_row.set_enable_expansion(settings.fetch_artwork)
        self._fetch_art_row.set_expanded(settings.fetch_artwork)
        self._fetch_art_row.connect(
            'notify::enable-expansion', self._on_fetch_art_toggled
        )

        self._art_caa_row = Adw.SwitchRow(
            title='Cover Art Archive',
            subtitle='MusicBrainz-linked release art',
            active=settings.artwork_source_caa,
        )
        self._art_caa_row.connect('notify::active', self._on_art_caa_toggled)
        self._fetch_art_row.add_row(self._art_caa_row)

        self._art_deezer_row = Adw.SwitchRow(
            title='Deezer',
            subtitle='Deezer catalog album covers',
            active=settings.artwork_source_deezer,
        )
        self._art_deezer_row.connect('notify::active', self._on_art_deezer_toggled)
        self._fetch_art_row.add_row(self._art_deezer_row)

        self._art_itunes_row = Adw.SwitchRow(
            title='iTunes / Apple Music',
            subtitle='High-resolution store artwork',
            active=settings.artwork_source_itunes,
        )
        self._art_itunes_row.connect('notify::active', self._on_art_itunes_toggled)
        self._fetch_art_row.add_row(self._art_itunes_row)

        # Embed lives under Download artwork (same expander group).
        self._embed_art_row = Adw.SwitchRow(
            title='Embed artwork',
            subtitle='Write cover image into ripped files',
            active=settings.embed_artwork,
        )
        self._embed_art_row.connect('notify::active', self._on_embed_art_toggled)
        self._fetch_art_row.add_row(self._embed_art_row)

        self._art_size_row = Adw.ComboRow(title='Embed size')
        self._art_size_values = [px for px, _label in ARTWORK_SIZES]
        self._art_size_row.set_model(
            Gtk.StringList.new([label for _px, label in ARTWORK_SIZES])
        )
        try:
            self._art_size_row.set_selected(
                self._art_size_values.index(settings.artwork_max_size)
            )
        except ValueError:
            try:
                self._art_size_row.set_selected(self._art_size_values.index(600))
            except ValueError:
                self._art_size_row.set_selected(2)
        self._art_size_row.connect('notify::selected', self._on_art_size_changed)
        self._art_size_row.set_sensitive(settings.embed_artwork)
        self._fetch_art_row.add_row(self._art_size_row)

        self._add_metadata_row(self._fetch_art_row)
        self._sync_artwork_source_sensitivity(settings.fetch_artwork)

        # ReplayGain last among metadata options
        self._rg_row = Adw.SwitchRow(
            title='ReplayGain',
            subtitle='Track + album loudness tags',
            active=settings.apply_replaygain,
        )
        self._rg_row.connect('notify::active', self._on_rg_toggled)
        self._add_metadata_row(self._rg_row)

    def sync_options_from_store(self) -> None:
        """Refresh sidebar widgets from settings (e.g. after Drive setup)."""
        settings = self.store.get()
        ids = [e[0] for e in ENCODERS]
        default_out = default_output_directory()

        handlers = [
            (self._output_row, self._on_output_changed),
            (self._device_row, self._on_device_path_changed),
            (self._folder_template_row, self._on_folder_template_changed),
            (self._filename_row, self._on_filename_changed),
            (self._encoder_row, self._on_encoder_changed),
            (self._flac_row, self._on_flac_changed),
            (self._mp3_row, self._on_mp3_changed),
            (self._opus_row, self._on_opus_changed),
            (self._offset_row, self._on_offset_changed),
            (self._test_copy_row, self._on_test_copy_toggled),
            (self._htoa_row, self._on_htoa_toggled),
            (self._copy_image_row, self._on_copy_image_toggled),
            (self._ar_row, self._on_ar_toggled),
            (self._burst_row, self._on_burst_toggled),
            (self._log_row, self._on_log_toggled),
            (self._cue_row, self._on_cue_toggled),
            (self._auto_rip_row, self._on_auto_rip_toggled),
            (self._auto_eject_row, self._on_auto_eject_toggled),
            (self._mb_row, self._on_mb_toggled),
            (self._freedb_row, self._on_freedb_toggled),
            (self._auto_lookup_row, self._on_auto_lookup_toggled),
            (self._rg_row, self._on_rg_toggled),
            (self._embed_art_row, self._on_embed_art_toggled),
            (self._fetch_art_row, self._on_fetch_art_toggled),
            (self._art_itunes_row, self._on_art_itunes_toggled),
            (self._art_caa_row, self._on_art_caa_toggled),
            (self._art_deezer_row, self._on_art_deezer_toggled),
            (self._art_size_row, self._on_art_size_changed),
        ]
        for widget, handler in handlers:
            widget.handler_block_by_func(handler)
        try:
            self._output_row.set_text(settings.output_directory or default_out)
            self._device_row.set_text(settings.device or '/dev/sr0')
            self._folder_template_row.set_text(settings.album_folder_template)
            self._filename_row.set_text(settings.filename_template)
            try:
                self._encoder_row.set_selected(ids.index(settings.encode_format))
            except ValueError:
                pass
            self._flac_row.set_value(settings.flac_compression)
            try:
                self._mp3_row.set_selected(
                    self._mp3_values.index(settings.mp3_bitrate)
                )
            except ValueError:
                pass
            self._opus_row.set_value(settings.opus_bitrate)
            self._update_quality_sensitivity(settings.encode_format)
            self._offset_row.set_value(settings.drive_sample_offset)
            status = (
                f'Configured for {settings.drive_offset_device or "this drive"}'
                if settings.drive_offset_configured
                else 'Not calibrated — run Drive setup in the Drive panel'
            )
            self._offset_row.set_subtitle(status)
            self._test_copy_row.set_active(settings.test_and_copy)
            self._htoa_row.set_active(settings.rip_htoa)
            self._copy_image_row.set_active(settings.copy_image)
            self._ar_row.set_active(settings.verify_accuraterip)
            self._burst_row.set_active(settings.burst_fallback)
            self._log_row.set_active(settings.write_rip_log)
            self._cue_row.set_active(settings.write_cue_file)
            self._auto_rip_row.set_active(settings.auto_rip)
            self._auto_eject_row.set_active(settings.auto_eject)
            self._auto_lookup_row.set_enable_expansion(
                settings.auto_lookup_metadata
            )
            self._mb_row.set_active(settings.use_musicbrainz)
            self._freedb_row.set_active(settings.use_freedb)
            self._sync_lookup_source_sensitivity(settings.auto_lookup_metadata)
            self._rg_row.set_active(settings.apply_replaygain)
            self._embed_art_row.set_active(settings.embed_artwork)
            self._art_size_row.set_sensitive(settings.embed_artwork)
            self._fetch_art_row.set_enable_expansion(settings.fetch_artwork)
            self._art_caa_row.set_active(settings.artwork_source_caa)
            self._art_deezer_row.set_active(settings.artwork_source_deezer)
            self._art_itunes_row.set_active(settings.artwork_source_itunes)
            self._sync_artwork_source_sensitivity(settings.fetch_artwork)
            try:
                self._art_size_row.set_selected(
                    self._art_size_values.index(settings.artwork_max_size)
                )
            except ValueError:
                pass
        finally:
            for widget, handler in handlers:
                widget.handler_unblock_by_func(handler)

        self._device = settings.device
        self._update_calibration_row()
        self._rebuild_drive_rows(
            settings.device or self._device or '/dev/sr0',
            allow_optical=False,
        )
        if self._artwork is not None:
            self._prepare_embed_artwork()

    def _update_quality_sensitivity(self, fmt: str) -> None:
        self._flac_row.set_sensitive(fmt == 'flac')
        self._mp3_row.set_sensitive(fmt == 'mp3')
        self._opus_row.set_sensitive(fmt == 'opus')

    def _on_output_changed(self, row: Adw.EntryRow) -> None:
        text = row.get_text().strip()
        if not text:
            text = default_output_directory()
            row.set_text(text)
        self.store.update(output_directory=text)

    def _on_device_path_changed(self, row: Adw.EntryRow) -> None:
        from ready2rip.util import validate_device_path

        text = row.get_text().strip() or '/dev/sr0'
        try:
            text = validate_device_path(text)
        except ValueError as exc:
            self._toast(str(exc))
            row.set_text(self.store.get().device or '/dev/sr0')
            return
        if row.get_text() != text:
            row.set_text(text)
        self.store.update(device=text)
        self._device = text
        self._rebuild_drive_rows(text, allow_optical=False)
        # Full TOC probe off the UI thread when the user changes the device path.
        self._refresh_disc(from_monitor=False)

    def _on_folder_template_changed(self, row: Adw.EntryRow) -> None:
        self.store.update(
            album_folder_template=row.get_text().strip()
            or '{album_artist}/{album}/{disc_folder}'
        )

    def _on_filename_changed(self, row: Adw.EntryRow) -> None:
        self.store.update(
            filename_template=row.get_text().strip() or '{track:02d} - {title}'
        )

    def _on_encoder_changed(self, row: Adw.ComboRow, *_args) -> None:
        idx = row.get_selected()
        if 0 <= idx < len(ENCODERS):
            fmt = ENCODERS[idx][0]
            self.store.update(encode_format=fmt)
            self._update_quality_sensitivity(fmt)

    def _on_flac_changed(self, row: Adw.SpinRow) -> None:
        self.store.update(flac_compression=int(row.get_value()))

    def _on_mp3_changed(self, row: Adw.ComboRow, *_args) -> None:
        idx = row.get_selected()
        if 0 <= idx < len(self._mp3_values):
            self.store.update(mp3_bitrate=self._mp3_values[idx])

    def _on_opus_changed(self, row: Adw.SpinRow) -> None:
        self.store.update(opus_bitrate=int(row.get_value()))

    def _on_offset_changed(self, row: Adw.SpinRow) -> None:
        device = self.store.get().device or self._device or '/dev/sr0'
        self.store.update(
            drive_sample_offset=int(row.get_value()),
            drive_offset_configured=True,
            drive_offset_device=device,
        )
        try:
            Gio.Settings.sync()
        except Exception:  # noqa: BLE001
            pass
        row.set_subtitle(f'Configured for {device}')

    def _on_mb_toggled(self, row: Adw.SwitchRow, *_args) -> None:
        self.store.update(use_musicbrainz=row.get_active())

    def _on_freedb_toggled(self, row: Adw.SwitchRow, *_args) -> None:
        self.store.update(use_freedb=row.get_active())

    def _on_auto_lookup_toggled(self, row: Adw.ExpanderRow, *_args) -> None:
        enabled = row.get_enable_expansion()
        self.store.update(auto_lookup_metadata=enabled)
        self._sync_lookup_source_sensitivity(enabled)
        if enabled and not row.get_expanded():
            row.set_expanded(True)

    def _sync_lookup_source_sensitivity(self, lookup_enabled: bool) -> None:
        for row in (self._mb_row, self._freedb_row):
            row.set_sensitive(lookup_enabled)

    def _on_test_copy_toggled(self, row: Adw.SwitchRow, *_args) -> None:
        self.store.update(test_and_copy=row.get_active())

    def _on_htoa_toggled(self, row: Adw.SwitchRow, *_args) -> None:
        self.store.update(rip_htoa=row.get_active())

    def _on_copy_image_toggled(self, row: Adw.SwitchRow, *_args) -> None:
        self.store.update(copy_image=row.get_active())
        if row.get_active():
            self._toast('Copy Image on — rip will write one continuous disc image')

    def _on_rg_toggled(self, row: Adw.SwitchRow, *_args) -> None:
        self.store.update(apply_replaygain=row.get_active())

    def _on_ar_toggled(self, row: Adw.SwitchRow, *_args) -> None:
        self.store.update(verify_accuraterip=row.get_active())

    def _on_burst_toggled(self, row: Adw.SwitchRow, *_args) -> None:
        self.store.update(burst_fallback=row.get_active())

    def _on_log_toggled(self, row: Adw.SwitchRow, *_args) -> None:
        self.store.update(write_rip_log=row.get_active())

    def _on_cue_toggled(self, row: Adw.SwitchRow, *_args) -> None:
        self.store.update(write_cue_file=row.get_active())
        if row.get_active():
            self._toast('Write .cue on — CUE sheet after secure rips')

    def _on_auto_rip_toggled(self, row: Adw.SwitchRow, *_args) -> None:
        self.store.update(auto_rip=row.get_active())
        if row.get_active():
            self._toast('Auto-rip on — insert a disc to start')
        else:
            self._auto_rip_pending = False

    def _on_auto_eject_toggled(self, row: Adw.SwitchRow, *_args) -> None:
        self.store.update(auto_eject=row.get_active())

    def _on_embed_art_toggled(self, row: Adw.SwitchRow, *_args) -> None:
        enabled = row.get_active()
        self.store.update(embed_artwork=enabled)
        self._art_size_row.set_sensitive(enabled)

    def _on_fetch_art_toggled(self, row: Adw.ExpanderRow, *_args) -> None:
        enabled = row.get_enable_expansion()
        self.store.update(fetch_artwork=enabled)
        self._sync_artwork_source_sensitivity(enabled)
        if enabled and not row.get_expanded():
            row.set_expanded(True)
        if enabled and self._album is not None and self._artwork is None:
            self._start_artwork_fetch(self._album)

    def _on_art_itunes_toggled(self, row: Adw.SwitchRow, *_args) -> None:
        self.store.update(artwork_source_itunes=row.get_active())
        self._maybe_refetch_artwork()

    def _on_art_caa_toggled(self, row: Adw.SwitchRow, *_args) -> None:
        self.store.update(artwork_source_caa=row.get_active())
        self._maybe_refetch_artwork()

    def _on_art_deezer_toggled(self, row: Adw.SwitchRow, *_args) -> None:
        self.store.update(artwork_source_deezer=row.get_active())
        self._maybe_refetch_artwork()

    def _sync_artwork_source_sensitivity(self, fetch_enabled: bool) -> None:
        for row in (
            self._art_caa_row,
            self._art_deezer_row,
            self._art_itunes_row,
        ):
            row.set_sensitive(fetch_enabled)

    def _artwork_source_options(self) -> ArtworkSourceOptions:
        s = self.store.get()
        return ArtworkSourceOptions(
            itunes=s.artwork_source_itunes,
            cover_art_archive=s.artwork_source_caa,
            deezer=s.artwork_source_deezer,
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

    def _on_art_size_changed(self, row: Adw.ComboRow, *_args) -> None:
        idx = row.get_selected()
        if 0 <= idx < len(self._art_size_values):
            self.store.update(artwork_max_size=self._art_size_values[idx])
            if self._artwork is not None:
                self._prepare_embed_artwork()
                emb = self._artwork_embed
                if emb is not None:
                    self._toast(
                        f'Embed size set to {emb.width}×{emb.height} '
                        f'(from {self._artwork.width}×{self._artwork.height} source)'
                    )

    # —— Header actions ——

    @Gtk.Template.Callback()
    def _on_eject_clicked(self, *_args) -> None:
        if self._ripping:
            self._toast('Cannot eject while ripping')
            return
        self._eject_disc()

    def _setup_lookup_menu(self) -> None:
        """Lookup menu: disc ID providers, or a pasted MusicBrainz release link."""
        disc_action = Gio.SimpleAction.new('lookup-disc', None)
        disc_action.connect('activate', self._on_lookup_disc)
        self.add_action(disc_action)

        mb_action = Gio.SimpleAction.new('lookup-mb-url', None)
        mb_action.connect('activate', self._on_lookup_mb_url)
        self.add_action(mb_action)

        menu = Gio.Menu()
        section = Gio.Menu()
        section.append('By disc ID', 'win.lookup-disc')
        section.append('MusicBrainz link…', 'win.lookup-mb-url')
        menu.append_section(None, section)
        self.lookup_button.set_menu_model(menu)

    # Appearance: system → light → dark (GNOME header icon button).
    def _setup_theme_button(self) -> None:
        """Header appearance toggle; relies on Adwaita for style changes."""
        settings = Gtk.Settings.get_default()
        if settings is not None:
            settings.set_property('gtk-enable-animations', True)
        scheme = normalize_color_scheme(self.store.get().color_scheme)
        apply_color_scheme(scheme)
        self._sync_theme_button(scheme)

    def _sync_theme_button(self, scheme: str) -> None:
        """Icon + tooltip for the current mode (next mode on click)."""
        scheme = normalize_color_scheme(scheme)
        icon = COLOR_SCHEME_ICONS[scheme]
        theme = Gtk.IconTheme.get_for_display(self.get_display())
        if not theme.has_icon(icon):
            for fallback in (
                'display-brightness-symbolic',
                'preferences-desktop-display-symbolic',
            ):
                if theme.has_icon(fallback):
                    icon = fallback
                    break
        self.theme_button.set_icon_name(icon)
        label = COLOR_SCHEME_LABELS[scheme]
        idx = COLOR_SCHEMES.index(scheme)
        nxt = COLOR_SCHEME_LABELS[COLOR_SCHEMES[(idx + 1) % len(COLOR_SCHEMES)]]
        self.theme_button.set_tooltip_text(
            f'Appearance: {label} (click for {nxt})'
        )

    @Gtk.Template.Callback()
    def _on_theme_clicked(self, *_args) -> None:
        """Cycle system → light → dark."""
        scheme = next_color_scheme(self.store.get().color_scheme)
        self.store.update(color_scheme=scheme)
        apply_color_scheme(scheme)
        self._sync_theme_button(scheme)

    def _on_lookup_disc(self, *_args) -> None:
        """MusicBrainz + FreeDB lookup from the inserted disc’s identifiers."""
        if self._ripping or self._looking_up:
            return
        self._start_metadata_lookup(interactive=True)

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

    @Gtk.Template.Callback()
    def _on_rip_clicked(self, *_args) -> None:
        if self._ripping:
            if self._rip_engine is not None:
                self._rip_engine.cancel()
                self.rip_title_label.set_label('Cancelling')
                self.rip_status_label.set_label('Stopping extraction…')
            return
        self._start_rip()

    def _selected_track_numbers(self) -> list[int]:
        selected = [
            num for num, check in sorted(self._track_checks.items()) if check.get_active()
        ]
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
        # Full-res for folder cover; embed-sized for files. Fetch before rip if missing.
        folder_art = self._artwork
        embed_art = self._artwork_embed or self._artwork
        need_art = settings.embed_artwork or settings.fetch_artwork
        if need_art and folder_art is None and self._album is not None:
            self._show_progress_panel(title='Preparing')
            self.rip_status_label.set_label('Fetching artwork…')
            self._set_progress_fraction(0.0)
            self._set_progress_style(None)
            try:
                full = ArtworkFetcher().fetch_best(
                    self._album,
                    sources=self._artwork_source_options(),
                )
                if full is not None:
                    self._artwork = full
                    self._prepare_embed_artwork()
                    folder_art = full
                    embed_art = self._artwork_embed or full
                    self._show_artwork(full)
            except Exception:  # noqa: BLE001
                pass
        elif folder_art is not None and embed_art is None:
            self._prepare_embed_artwork()
            embed_art = self._artwork_embed or folder_art

        from ready2rip.util import validate_device_path

        try:
            device = validate_device_path(
                settings.device or self._device or '/dev/sr0'
            )
        except ValueError as exc:
            self._toast(str(exc))
            self._set_ripping_ui(False)
            self._ripping = False
            return

        job = RipJob(
            device=device,
            track_numbers=track_numbers,
            output_directory=settings.resolved_output_directory(),
            encode_format=settings.encode_format,
            flac_compression=settings.flac_compression,
            mp3_bitrate=settings.mp3_bitrate,
            opus_bitrate=settings.opus_bitrate,
            apply_replaygain=settings.apply_replaygain,
            embed_artwork=settings.embed_artwork,
            artwork=embed_art if settings.embed_artwork else None,
            folder_artwork=folder_art,
            album=self._album,
            filename_template=settings.filename_template,
            album_folder_template=settings.album_folder_template,
            # Always write full-size cover into the album folder when we have art.
            save_cover_file=folder_art is not None,
            verify_accuraterip=settings.verify_accuraterip,
            sample_offset=settings.drive_sample_offset,
            disc_info=self._disc,
            disc_track_count=self._disc.track_count,
            burst_fallback=settings.burst_fallback,
            write_rip_log=settings.write_rip_log,
            write_cue_file=settings.write_cue_file,
            test_and_copy=settings.test_and_copy,
            drive_caches_audio=(
                settings.drive_caches_audio
                if settings.drive_cache_configured
                else None
            ),
            drive_cache_message=settings.drive_cache_message,
            drive_accurate_stream=(
                settings.drive_accurate_stream
                if settings.drive_accurate_stream_configured
                else None
            ),
            drive_accurate_stream_message=settings.drive_accurate_stream_message,
            defeat_audio_cache=(
                settings.defeat_audio_cache or settings.drive_caches_audio
            ),
            rip_htoa=settings.rip_htoa,
            copy_image=settings.copy_image,
            artwork_max_size=settings.artwork_max_size,
        )

        self._ripping = True
        self._rip_engine = RipEngine()
        self._set_ripping_ui(True)
        fmt = job.encode_format.upper()
        n = len(job.track_numbers)
        if job.copy_image:
            self.rip_title_label.set_label('Copy Image')
            self.rip_status_label.set_label(f'Starting disc image ({fmt})…')
        else:
            self.rip_title_label.set_label(
                f'Ripping {n} track{"s" if n != 1 else ""}'
            )
            self.rip_status_label.set_label(f'Starting {fmt} extraction…')
        self._set_progress_fraction(0.0)
        self._set_progress_style(None)

        engine = self._rip_engine

        def on_progress(progress: RipProgress) -> None:
            GLib.idle_add(self._on_rip_progress, progress)

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
        frac = max(0.0, min(1.0, fraction))
        self.rip_progress.set_fraction(frac)
        self.rip_percent_label.set_label(f'{int(frac * 100)}%')

    def _set_progress_style(self, state: str | None) -> None:
        """state: None | 'success' | 'error'"""
        self.rip_progress.remove_css_class('success')
        self.rip_progress.remove_css_class('error')
        if state:
            self.rip_progress.add_css_class(state)

    def _show_progress_panel(self, *, title: str | None = None) -> None:
        self._cancel_progress_hide()
        if title:
            self.rip_title_label.set_label(title)
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
            if not self._ripping:
                _hide_now()
            return GLib.SOURCE_REMOVE

        self._progress_hide_id = GLib.timeout_add(delay_ms, _hide)

    def _finish_hide_bottom_bar(self) -> bool:
        if not self._ripping and not self.rip_revealer.get_reveal_child():
            self.toolbar_view.set_reveal_bottom_bars(False)
        return GLib.SOURCE_REMOVE

    def _set_lookup_controls_sensitive(self, sensitive: bool) -> None:
        """Enable/disable the Lookup menu (disc ID + MusicBrainz link)."""
        self.lookup_button.set_sensitive(sensitive)

    def _set_ripping_ui(self, active: bool) -> None:
        if active:
            self._show_progress_panel(title='Ripping')
        self.eject_button.set_sensitive(not active)
        self._set_lookup_controls_sensitive(not active and self._disc is not None)
        if active:
            self.rip_button.set_label('Cancel')
            self.rip_button.set_sensitive(True)
            self.rip_button.remove_css_class('suggested-action')
            self.rip_button.add_css_class('destructive-action')
        else:
            self.rip_button.set_label('Rip CD')
            self.rip_button.remove_css_class('destructive-action')
            self.rip_button.add_css_class('suggested-action')
            self.rip_button.set_sensitive(self._disc is not None and bool(self._disc.tracks))

    def _on_rip_progress(self, progress: RipProgress) -> bool:
        if not self._ripping:
            return GLib.SOURCE_REMOVE
        self._set_progress_fraction(progress.fraction)
        if progress.message:
            self.rip_status_label.set_label(progress.message)
        # Calm heading; track detail lives in the status line underneath.
        if progress.state == RipState.REPLAYGAIN:
            self.rip_title_label.set_label('ReplayGain')
        elif progress.state == RipState.ENCODING:
            self.rip_title_label.set_label('Encoding')
        elif progress.state == RipState.VERIFYING:
            self.rip_title_label.set_label('Verifying')
        elif progress.state == RipState.PREPARING:
            self.rip_title_label.set_label('Preparing')
        elif progress.state == RipState.RIPPING:
            self.rip_title_label.set_label('Ripping')
        elif progress.state == RipState.TAGGING:
            self.rip_title_label.set_label('Tagging')
        if progress.state == RipState.FAILED:
            self.rip_title_label.set_label('Rip failed')
            self.rip_status_label.set_label(progress.message or 'Rip failed')
            self._set_progress_style('error')
        return GLib.SOURCE_REMOVE

    def _on_rip_finished(self, result: RipResult) -> None:
        self._ripping = False
        self._rip_engine = None
        self._set_ripping_ui(False)

        if result.cancelled:
            self.rip_title_label.set_label('Cancelled')
            self.rip_status_label.set_label('Rip cancelled')
            self._set_progress_style(None)
            self._hide_progress_panel(delay_ms=2500)
            self._toast('Rip cancelled')
            return

        if not result.success:
            self._show_progress_panel(title='Rip failed')
            self.rip_status_label.set_label(result.error or 'Rip failed')
            self._set_progress_fraction(0.0)
            self._set_progress_style('error')
            self._hide_progress_panel(delay_ms=8000)
            self._toast(result.error or 'Rip failed')
            return

        n = len(result.output_files)
        dest = str(result.album_dir) if result.album_dir else 'output folder'
        ar_bits = []
        if result.htoa_ripped:
            ar_bits.append('HTOA')
        if result.cover_path is not None:
            ar_bits.append('cover saved')
        if result.cache_result is not None and result.cache_result.caches:
            ar_bits.append('cache defeated')
        if result.accuraterip:
            from ready2rip.accuraterip import AccurateRipConfidence

            matches = sum(
                1
                for r in result.accuraterip
                if r.confidence == AccurateRipConfidence.MATCH
            )
            ar_bits.append(f'AR {matches}/{len(result.accuraterip)}')
        if result.burst_tracks:
            ar_bits.append(f'burst on {len(result.burst_tracks)}')
        if result.log_path is not None:
            ar_bits.append('log saved')
        detail = f'{n} file{"s" if n != 1 else ""} saved to {dest}'
        if ar_bits:
            detail = f'{detail} · {", ".join(ar_bits)}'
        self._show_progress_panel(title='Rip complete')
        self.rip_status_label.set_label(detail)
        self._set_progress_fraction(1.0)
        self._set_progress_style('success')
        self._hide_progress_panel(delay_ms=7000)
        self._toast(f'Rip complete · {n} file(s)', timeout=4)
        # Show per-track AR outcome in the track list subtitles when available.
        if result.accuraterip:
            self._apply_ar_results_to_rows(result.accuraterip)

        if self.store.get().auto_eject:
            GLib.timeout_add(800, self._do_auto_eject)

    def _eject_disc(self) -> None:
        """Open the tray / eject media on the configured optical device."""
        device = self.store.get().device or self._device or '/dev/sr0'
        ok, message = eject_drive(device)
        if ok:
            self._toast('Disc ejected')
            self._last_tray_state = DriveTrayState.TRAY_OPEN
            self._clear_disc_ui_for_empty(
                title='Tray open',
                description='Disc ejected. Close the tray or insert another CD.',
            )
        else:
            self._toast(f'Eject failed: {message}')

    def _do_auto_eject(self) -> bool:
        self._eject_disc()
        return GLib.SOURCE_REMOVE

    # —— Drive monitor / disc load ——

    def _start_drive_monitor(self) -> None:
        """Poll tray/media state so we notice open trays and new discs."""
        if self._poll_id is not None:
            return
        self._poll_drive(force_refresh=False)
        self._poll_id = GLib.timeout_add_seconds(2, self._on_drive_poll)

    def _on_drive_poll(self) -> bool:
        self._poll_drive(force_refresh=False)
        return GLib.SOURCE_CONTINUE

    def _poll_drive(self, *, force_refresh: bool) -> None:
        """Schedule a non-blocking tray check (never ioctl/cdparanoia on GTK thread)."""
        if self._ripping or self._probe_in_flight:
            return
        device = self.store.get().device or self._device or '/dev/sr0'
        self._device = device
        # Capture generation so a newer probe can supersede this poll.
        self._poll_generation += 1
        generation = self._poll_generation

        def work() -> None:
            try:
                status = query_drive_status(device)
            except Exception as exc:  # noqa: BLE001
                import logging

                logging.getLogger(__name__).debug('Drive poll failed: %s', exc)
                return

            def finish() -> bool:
                if generation != self._poll_generation:
                    return GLib.SOURCE_REMOVE
                if self._ripping or self._probe_in_flight:
                    return GLib.SOURCE_REMOVE
                self._handle_drive_poll_result(
                    device, status, force_refresh=force_refresh
                )
                return GLib.SOURCE_REMOVE

            GLib.idle_add(finish)

        threading.Thread(
            target=work, daemon=True, name='ready2rip-drive-poll'
        ).start()

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

        if state is DriveTrayState.TRAY_OPEN:
            if prev is not DriveTrayState.TRAY_OPEN:
                self._auto_rip_disc_key = None
                self._auto_rip_pending = False
                self._clear_disc_ui_for_empty(
                    title='Tray open',
                    description=(
                        f'The drive tray is open on {device}. '
                        'Insert a disc and close the tray.'
                    ),
                )
            return

        if state is DriveTrayState.NO_DISC:
            if prev is not DriveTrayState.NO_DISC:
                self._auto_rip_disc_key = None
                self._auto_rip_pending = False
                self._clear_disc_ui_for_empty(
                    title='No disc',
                    description=(
                        f'Tray is closed but no disc is in {device}. '
                        'Insert an audio CD.'
                    ),
                )
            return

        if state is DriveTrayState.NOT_READY:
            if prev is not DriveTrayState.NOT_READY:
                if self.stack.get_visible_child_name() == 'empty':
                    self.status_page.set_title('Drive not ready')
                    self.status_page.set_description(
                        'Waiting for the drive to finish spinning up…'
                    )
            return

        if state is DriveTrayState.MISSING:
            if prev is not DriveTrayState.MISSING:
                self._clear_disc_ui_for_empty(
                    title='Drive not found',
                    description=(
                        f'Cannot open {device}. Check Optical device path in Rip options.'
                    ),
                )
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
        device = status.device or self._device or '/dev/sr0'
        # Never call cdparanoia from the GTK thread.
        self._rebuild_drive_rows(device, allow_optical=False)

    def _clear_disc_ui_for_empty(self, *, title: str, description: str) -> None:
        self._disc = None
        self._ids = None
        self._album = None
        self._clear_artwork()
        self.stack.set_visible_child_name('empty')
        self.rip_button.set_sensitive(False)
        self._set_lookup_controls_sensitive(False)
        self.status_page.set_title(title)
        self.status_page.set_description(description)
        self._rebuild_track_list(None)
        self._fill_album_edit_fields(None)
        self._rebuild_disc_rows(None, None)
        self._rebuild_drive_rows(
            self.store.get().device or self._device or '/dev/sr0',
            allow_optical=False,
        )
        self._update_album_header(None, None)

    def _disc_identity_key(
        self, info: DiscInfo, ids: DiscIdentifiers | None
    ) -> str:
        if ids is not None:
            if getattr(ids, 'musicbrainz_discid', None):
                return f'mb:{ids.musicbrainz_discid}'
            if getattr(ids, 'freedb_id', None):
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

        delay = 2500 if settings.auto_lookup_metadata else 800
        GLib.timeout_add(delay, _start)

    def _refresh_disc(self, *, from_monitor: bool = False) -> None:
        """Kick off a background probe (never blocks the GTK main loop)."""
        settings = self.store.get()
        device = settings.device or self._device or '/dev/sr0'
        self._device = device
        if not from_monitor and self.stack.get_visible_child_name() == 'empty':
            self.status_page.set_title('Looking for a disc')
            self.status_page.set_description(
                f'Looking for an audio CD on {device}…'
            )
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
        self._rebuild_drive_rows(
            device, drive_info=self._drive_info, allow_optical=False
        )

        if status.state is DriveTrayState.TRAY_OPEN:
            self._clear_disc_ui_for_empty(
                title='Tray open',
                description=(
                    f'The drive tray is open on {device}. '
                    'Insert a disc and close the tray.'
                ),
            )
            return
        if status.state is DriveTrayState.NO_DISC:
            self._clear_disc_ui_for_empty(
                title='No disc',
                description=(
                    f'Tray is closed but no disc is in {device}. Insert an audio CD.'
                ),
            )
            return
        if status.state is DriveTrayState.MISSING:
            self._clear_disc_ui_for_empty(
                title='Drive not found',
                description=(
                    f'Cannot open {device}. Check Optical device path in Rip options.'
                ),
            )
            return

        self._disc = info
        self._album = None
        self._ids = None
        self._clear_artwork()

        if info is None or not info.tracks:
            self.stack.set_visible_child_name('empty')
            self.rip_button.set_sensitive(False)
            self._set_lookup_controls_sensitive(False)
            if status.state is DriveTrayState.NOT_READY:
                self.status_page.set_title('Drive not ready')
                self.status_page.set_description(
                    'Waiting for the drive to finish loading the disc…'
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
        self.rip_button.set_sensitive(True)
        self._set_lookup_controls_sensitive(not self._ripping and not self._looking_up)

        # Restore cached metadata for this disc when available.
        cached_album, cached_art = self._meta_cache.load(info, self._ids)
        if cached_album is not None and self._meta_cache.has_useful_metadata(
            cached_album
        ):
            self._album = cached_album
            restored = True
        else:
            self._album = self._blank_album_for_disc(info)
            restored = False

        self._rebuild_disc_rows(info, self._ids)
        self._rebuild_drive_rows(
            device, drive_info=self._drive_info, allow_optical=False
        )
        self._fill_album_edit_fields(self._album)
        self._rebuild_track_list(info)
        self._update_album_header(info, self._album)

        if cached_art is not None:
            self._art_generation += 1
            self._artwork = cached_art
            self._prepare_embed_artwork()
            self._show_artwork(cached_art)

        if restored:
            self._toast(
                f'Restored cached metadata · {info.track_count} tracks',
                timeout=3,
            )
        else:
            self._toast(f'Found {info.track_count} audio tracks', timeout=3)

        # Only auto-lookup when we have nothing useful cached.
        if settings.auto_lookup_metadata and not restored:
            self._start_metadata_lookup(interactive=False)

        # Auto-rip when a new disc is detected (especially via monitor).
        disc_key = self._disc_identity_key(info, self._ids)
        if settings.auto_rip:
            self._maybe_schedule_auto_rip(disc_key)

    @staticmethod
    def _format_art_origin(source: str | None) -> str:
        """Human label for where the cover image came from."""
        raw = (source or '').strip()
        if not raw:
            return 'Album art from unknown source'
        # Resize variants: "itunes/embed-600" → itunes
        base = raw.split('/', 1)[0].strip().lower()
        names = {
            'itunes': 'iTunes',
            'apple': 'iTunes',
            'deezer': 'Deezer',
            'coverartarchive': 'Cover Art Archive',
            'caa': 'Cover Art Archive',
            'local': 'user',
            'file': 'user',
            'user': 'user',
            'direct': 'URL',
            'url': 'URL',
            'cache': 'cache',
            'cached': 'cache',
            'musicbrainz': 'Cover Art Archive',
        }
        pretty = names.get(base)
        if pretty is None:
            pretty = base.replace('_', ' ').replace('-', ' ').strip() or 'unknown source'
        return f'Album art from {pretty}'

    def _update_art_info(self, status: str | None = None) -> None:
        """Two lines under art: size/embed, then origin (or a status message)."""
        if status is not None:
            self.art_size_label.set_label(status)
            self.art_origin_label.set_label('')
            self.art_origin_label.set_visible(False)
            return
        image = self._artwork
        if image is None:
            self.art_size_label.set_label('No artwork')
            self.art_origin_label.set_label('')
            self.art_origin_label.set_visible(False)
            return
        size_bits = [f'{image.width}×{image.height}']
        emb = self._artwork_embed
        if emb is not None:
            size_bits.append(f'embed {emb.width}×{emb.height}')
        self.art_size_label.set_label(' · '.join(size_bits))
        self.art_origin_label.set_label(self._format_art_origin(image.source))
        self.art_origin_label.set_visible(True)

    @staticmethod
    def _format_metadata_origin(source: str | None) -> str:
        """Human label for the metadata origin banner."""
        raw = (source or '').strip().lower()
        if not raw or raw == 'manual':
            return 'No metadata'
        # Manual edits after a lookup: musicbrainz+manual, freedb+manual, …
        base = raw.split('+', 1)[0].strip()
        names = {
            'musicbrainz': 'MusicBrainz',
            'freedb': 'FreeDB',
            'gnudb': 'gnudb',
            'cd-text': 'CD-TEXT',
            'cdtext': 'CD-TEXT',
            'cache': 'cache',
        }
        pretty = names.get(base)
        if pretty is None:
            pretty = base.replace('_', ' ').replace('-', ' ').strip().title() or 'unknown'
        if '+manual' in raw or raw.endswith('+manual'):
            return f'Metadata from {pretty} (edited)'
        return f'Metadata from {pretty}'

    def _update_album_header(self, info: DiscInfo | None, album: AlbumMetadata | None) -> None:
        """Refresh metadata origin banner and cover placeholder when needed."""
        source = album.source if album is not None else None
        self.metadata_origin_label.set_label(self._format_metadata_origin(source))
        if self._artwork is None:
            self._show_placeholder_cover()
            self._update_art_info()

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

        self.disc_group.set_description(
            f'{info.track_count} tracks · {info.device}'
        )

        rows = [
            ('Device', info.device),
            ('Audio tracks', str(info.track_count)),
        ]
        if ids and ids.musicbrainz_discid:
            rows.append(('MusicBrainz DiscID', ids.musicbrainz_discid))
        if ids and ids.freedb_id:
            rows.append(('FreeDB disc ID', ids.freedb_id))

        total_sectors = sum(t.length_sectors for t in info.tracks)
        minutes, seconds = divmod(int(round(total_sectors / 75.0)), 60)
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

        settings = self.store.get()
        info = drive_info if drive_info is not None else self._drive_info
        if info is None or info.device != device:
            try:
                info = probe_drive(device, allow_optical=allow_optical)
            except Exception as exc:  # noqa: BLE001
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
            self.drive_group.add(tray_row)
            self._drive_rows.append(tray_row)
        else:
            tray_row = Adw.ActionRow(
                title='Tray / media',
                subtitle='Checking…',
            )
            self.drive_group.add(tray_row)
            self._drive_rows.append(tray_row)

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

        if settings.drive_accurate_stream_configured:
            astream_sub = 'Yes' if settings.drive_accurate_stream else 'No'
            if settings.drive_accurate_stream_message:
                astream_sub = (
                    f'{astream_sub} · {settings.drive_accurate_stream_message}'
                )
        else:
            astream_sub = 'Not measured — run Drive setup'
        astream_row = Adw.ActionRow(title='Accurate Stream', subtitle=astream_sub)
        astream_row.set_tooltip_text(astream_sub)
        self.drive_group.add(astream_row)
        self._drive_rows.append(astream_row)

        if settings.drive_cache_configured:
            cache_sub = (
                'Yes — defeat between test and copy'
                if settings.drive_caches_audio
                else 'No clear audio cache'
            )
            if settings.drive_cache_message:
                cache_sub = f'{cache_sub}. {settings.drive_cache_message}'
        else:
            cache_sub = 'Not measured — run Drive setup'
        cache_row = Adw.ActionRow(title='Audio cache', subtitle=cache_sub)
        cache_row.set_tooltip_text(cache_sub)
        self.drive_group.add(cache_row)
        self._drive_rows.append(cache_row)

    def _on_run_drive_setup(self, *_args) -> None:
        from ready2rip.setup_dialog import DriveSetupDialog

        dialog = DriveSetupDialog(self.store)

        def on_closed(*_a) -> None:
            self.sync_options_from_store()
            self._update_calibration_row()
            device = self.store.get().device or self._device or '/dev/sr0'
            self._rebuild_drive_rows(device, allow_optical=False)

        dialog.connect('closed', on_closed)
        dialog.present(self)

    def _build_album_edit_rows(self) -> None:
        """Album EntryRows in a PreferencesGroup (same borders as Tracks)."""
        for row in self._album_field_rows:
            self.album_edit_group.remove(row)
        self._album_field_rows.clear()

        self.album_edit_group.set_title('Album')
        self.album_edit_group.set_description(None)

        self._album_title_row = Adw.EntryRow(title='Album')
        self._album_title_row.connect('changed', self._on_album_field_changed)
        self.album_edit_group.add(self._album_title_row)
        self._album_field_rows.append(self._album_title_row)

        self._album_artist_row = Adw.EntryRow(title='Album artist')
        self._album_artist_row.set_tooltip_text(
            'Multiple artists: separate with a semicolon '
            '(e.g. Artist One; Artist Two)'
        )
        self._album_artist_row.connect('changed', self._on_album_field_changed)
        self.album_edit_group.add(self._album_artist_row)
        self._album_field_rows.append(self._album_artist_row)

        self._album_date_row = Adw.EntryRow(title='Year')
        self._album_date_row.set_tooltip_text('Release date (e.g. 1997 or 1997-03-01)')
        self._album_date_row.connect('changed', self._on_album_field_changed)
        self.album_edit_group.add(self._album_date_row)
        self._album_field_rows.append(self._album_date_row)

        self._album_label_row = Adw.EntryRow(title='Label')
        self._album_label_row.connect('changed', self._on_album_field_changed)
        self.album_edit_group.add(self._album_label_row)
        self._album_field_rows.append(self._album_label_row)

        self._album_disc_row = Adw.EntryRow(title='Disc')
        self._album_disc_row.set_text('1/1')
        self._album_disc_row.set_tooltip_text(
            'Disc as N/M (e.g. 1/1 or 2/3) — DISCNUMBER / TPOS'
        )
        self._album_disc_row.connect('changed', self._on_album_field_changed)
        self.album_edit_group.add(self._album_disc_row)
        self._album_field_rows.append(self._album_disc_row)

    def _blank_album_for_disc(self, info: DiscInfo) -> AlbumMetadata:
        tracks = [
            TrackMetadata(number=t.number, title='', artist='')
            for t in info.tracks
        ]
        return AlbumMetadata(source='manual', tracks=tracks)

    def _ensure_album_tracks(self) -> AlbumMetadata:
        album = self._album
        if album is None:
            if self._disc is not None:
                album = self._blank_album_for_disc(self._disc)
            else:
                album = AlbumMetadata(source='manual')
            self._album = album
        if self._disc is not None:
            by_num = {t.number: t for t in album.tracks}
            new_tracks: list[TrackMetadata] = []
            for toc in self._disc.tracks:
                if toc.number in by_num:
                    new_tracks.append(by_num[toc.number])
                else:
                    new_tracks.append(
                        TrackMetadata(number=toc.number, title='', artist='')
                    )
            album.tracks = new_tracks
        return album

    def _fill_album_edit_fields(self, album: AlbumMetadata | None) -> None:
        from ready2rip.tags.artists import normalize_artists

        self._suppress_meta_write = True
        try:
            if self._album_title_row is not None:
                self._album_title_row.set_text(album.title if album else '')
            if self._album_artist_row is not None:
                self._album_artist_row.set_text(
                    normalize_artists(album.artist) if album else ''
                )
            if self._album_date_row is not None:
                self._album_date_row.set_text(album.date if album else '')
            if self._album_label_row is not None:
                self._album_label_row.set_text(album.label if album else '')
            disc_num = max(1, int(album.medium_position)) if album else 1
            disc_total = max(1, int(album.medium_count)) if album else 1
            if self._album_disc_row is not None:
                self._album_disc_row.set_text(f'{disc_num}/{disc_total}')
        finally:
            self._suppress_meta_write = False

    def _on_album_field_changed(self, *_args) -> None:
        if self._suppress_meta_write or self._disc is None:
            return
        from ready2rip.tags.artists import normalize_artists

        album = self._ensure_album_tracks()
        if self._album_title_row is not None:
            album.title = self._album_title_row.get_text().strip()
        if self._album_artist_row is not None:
            album.artist = normalize_artists(self._album_artist_row.get_text())
        if self._album_date_row is not None:
            album.date = self._album_date_row.get_text().strip()
        if self._album_label_row is not None:
            album.label = self._album_label_row.get_text().strip()
        if self._album_disc_row is not None:
            disc_num, disc_total = parse_disc_field(
                self._album_disc_row.get_text()
            )
            album.medium_position = disc_num
            album.medium_count = disc_total
            normalized = f'{disc_num}/{disc_total}'
            current = self._album_disc_row.get_text().strip()
            if current and current != normalized and '/' in current:
                left, _, right = current.partition('/')
                if left.strip().isdigit() and right.strip().isdigit():
                    self._suppress_meta_write = True
                    try:
                        self._album_disc_row.set_text(normalized)
                    finally:
                        self._suppress_meta_write = False
        _mark_album_edited(album)
        self._update_album_header(self._disc, album)
        self._schedule_cache_save()

    def _schedule_cache_save(self) -> None:
        """Debounce disk writes while typing in metadata fields."""
        if self._cache_save_id is not None:
            GLib.source_remove(self._cache_save_id)
            self._cache_save_id = None

        def _save() -> bool:
            self._cache_save_id = None
            self._save_metadata_cache()
            return GLib.SOURCE_REMOVE

        self._cache_save_id = GLib.timeout_add(400, _save)

    def _save_metadata_cache(self) -> None:
        if self._disc is None or self._album is None:
            return
        if not self._meta_cache.has_useful_metadata(self._album) and self._artwork is None:
            return
        self._meta_cache.save(
            self._disc,
            self._album,
            self._ids,
            artwork=self._artwork,
        )

    def _rebuild_track_list(self, info: DiscInfo | None) -> None:
        for row in self._track_rows:
            self.track_edit_group.remove(row)
        self._track_rows.clear()
        self._track_checks.clear()
        self._track_title_entries.clear()
        self._track_artist_entries.clear()
        self._track_title_labels.clear()
        self._track_artist_labels.clear()
        self._track_status_labels.clear()
        self._track_title_stacks = {}
        self._track_artist_stacks = {}
        self._track_edit_button = None
        self._track_fill_button = None
        # New disc/list always starts in view mode.
        self._tracks_editing = False
        # Fresh size groups so rebuilt widgets align cleanly.
        self._track_title_size = Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL)
        self._track_artist_size = Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL)

        if info is None:
            self.track_edit_group.set_header_suffix(None)
            self.track_edit_group.set_title('Tracks')
            self.track_edit_group.set_description(None)
            return

        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        btn_edit = Gtk.Button(label='Edit')
        btn_edit.add_css_class('flat')
        btn_edit.set_tooltip_text('Edit titles and artists')
        btn_edit.connect('clicked', self._on_tracks_edit_toggled)
        btn_fill = Gtk.Button(label='Fill')
        btn_fill.add_css_class('flat')
        btn_fill.set_tooltip_text(
            'Copy album artist into empty track artists'
        )
        btn_fill.set_sensitive(False)
        btn_fill.connect('clicked', lambda *_: self._fill_empty_track_artists())
        btn_all = Gtk.Button(label='All')
        btn_all.add_css_class('flat')
        btn_all.set_tooltip_text('Select all tracks')
        btn_all.connect('clicked', lambda *_: self._set_all_tracks(True))
        btn_none = Gtk.Button(label='None')
        btn_none.add_css_class('flat')
        btn_none.set_tooltip_text('Deselect all tracks')
        btn_none.connect('clicked', lambda *_: self._set_all_tracks(False))
        header_box.append(btn_edit)
        header_box.append(btn_fill)
        header_box.append(btn_all)
        header_box.append(btn_none)
        self.track_edit_group.set_header_suffix(header_box)
        self._track_edit_button = btn_edit
        self._track_fill_button = btn_fill

        # Column header: Title | Artist — keeps artist column aligned under “Tracks”.
        col_header = self._make_track_column_header()
        self.track_edit_group.add(col_header)
        self._track_rows.append(col_header)

        from ready2rip.tags.artists import normalize_artists

        album = self._ensure_album_tracks()
        album_artist = normalize_artists(album.artist or '')

        self._suppress_meta_write = True
        try:
            for track in info.tracks:
                meta = track_meta_for(album, track.number)
                title = meta.title if meta else ''
                artist = normalize_artists((meta.artist if meta else '') or '')
                if (
                    not artist
                    and album_artist
                    and self._meta_cache.has_useful_metadata(album)
                ):
                    artist = album_artist

                row = self._make_track_edit_row(
                    track.number,
                    title=title,
                    artist=artist,
                    duration_label=track.duration_label,
                )
                self.track_edit_group.add(row)
                self._track_rows.append(row)
        finally:
            self._suppress_meta_write = False

        self._apply_tracks_edit_mode()
        self.track_edit_group.set_title(f'Tracks · {info.track_count}')
        self.track_edit_group.set_description(None)

    def _make_track_column_header(self) -> Gtk.ListBoxRow:
        """Column titles so Title / Artist line up with track cells."""
        row = Gtk.ListBoxRow()
        row.set_activatable(False)
        row.set_selectable(False)
        row.add_css_class('ready2rip-track-header')

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        box.add_css_class('ready2rip-track-row')
        box.set_hexpand(True)

        # Spacer matching checkbox + track number width.
        lead = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        lead.add_css_class('ready2rip-track-lead')
        check_ph = Gtk.Label(label='')
        check_ph.set_size_request(18, -1)
        num_ph = Gtk.Label(label='#')
        num_ph.add_css_class('ready2rip-track-num')
        num_ph.add_css_class('caption')
        num_ph.add_css_class('dim-label')
        num_ph.set_xalign(0.5)
        lead.append(check_ph)
        lead.append(num_ph)
        box.append(lead)

        title_h = Gtk.Label(label='Title')
        title_h.add_css_class('caption')
        title_h.add_css_class('dim-label')
        title_h.add_css_class('heading')
        title_h.set_halign(Gtk.Align.START)
        title_h.set_xalign(0.0)
        title_h.set_hexpand(True)
        self._track_title_size.add_widget(title_h)
        box.append(title_h)

        artist_h = Gtk.Label(label='Artist')
        artist_h.add_css_class('caption')
        artist_h.add_css_class('dim-label')
        artist_h.add_css_class('heading')
        artist_h.set_halign(Gtk.Align.START)
        artist_h.set_xalign(0.0)
        artist_h.set_hexpand(True)
        self._track_artist_size.add_widget(artist_h)
        box.append(artist_h)

        dur_h = Gtk.Label(label='Time')
        dur_h.add_css_class('ready2rip-track-dur')
        dur_h.add_css_class('caption')
        dur_h.add_css_class('dim-label')
        dur_h.set_xalign(1.0)
        box.append(dur_h)

        row.set_child(box)
        return row

    def _make_track_edit_row(
        self,
        number: int,
        *,
        title: str,
        artist: str,
        duration_label: str,
    ) -> Gtk.ListBoxRow:
        """Flat track row: checkbox · # · title · artist · status · time.

        View mode shows labels; Edit mode swaps in entries for title/artist.
        Size groups keep Title / Artist columns aligned under the header.
        """
        row = Gtk.ListBoxRow()
        row.set_activatable(False)
        row.set_selectable(False)
        row.set_tooltip_text(f'Track {number:02d} · {duration_label}')

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        box.add_css_class('ready2rip-track-row')
        box.set_hexpand(True)

        lead = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        lead.add_css_class('ready2rip-track-lead')

        check = Gtk.CheckButton(active=True)
        check.set_valign(Gtk.Align.CENTER)
        check.set_tooltip_text(f'Include track {number:02d} in the rip')
        lead.append(check)

        num = Gtk.Label(label=f'{number:02d}')
        num.add_css_class('ready2rip-track-num')
        num.add_css_class('dim-label')
        num.set_valign(Gtk.Align.CENTER)
        num.set_xalign(0.5)
        lead.append(num)
        box.append(lead)

        # Title stack (label + entry share size group with other rows).
        title_stack = Gtk.Stack()
        title_stack.set_hexpand(True)
        title_stack.set_halign(Gtk.Align.FILL)
        title_stack.set_transition_type(Gtk.StackTransitionType.NONE)

        title_label = Gtk.Label(label=title or f'Track {number:02d}')
        title_label.set_hexpand(True)
        title_label.set_halign(Gtk.Align.START)
        title_label.set_xalign(0.0)
        title_label.set_ellipsize(Pango.EllipsizeMode.END)
        title_label.set_valign(Gtk.Align.CENTER)
        if not title:
            title_label.add_css_class('dim-label')
        title_stack.add_named(title_label, 'view')

        title_entry = Gtk.Entry()
        title_entry.set_text(title)
        title_entry.set_placeholder_text(f'Track {number:02d} title')
        title_entry.set_hexpand(True)
        title_entry.add_css_class('ready2rip-track-entry')
        title_entry.set_tooltip_text(f'Title for track {number:02d}')
        title_entry.connect(
            'changed',
            lambda *_a, n=number: self._on_track_field_changed(n),
        )
        title_stack.add_named(title_entry, 'edit')
        title_stack.set_visible_child_name('view')
        self._track_title_size.add_widget(title_stack)
        box.append(title_stack)

        # Artist stack — same width across all tracks (aligned under “Artist”).
        artist_stack = Gtk.Stack()
        artist_stack.set_hexpand(True)
        artist_stack.set_halign(Gtk.Align.FILL)
        artist_stack.set_transition_type(Gtk.StackTransitionType.NONE)

        artist_label = Gtk.Label(label=artist or '—')
        artist_label.set_hexpand(True)
        artist_label.set_halign(Gtk.Align.START)
        artist_label.set_xalign(0.0)
        artist_label.set_ellipsize(Pango.EllipsizeMode.END)
        artist_label.set_valign(Gtk.Align.CENTER)
        if not artist:
            artist_label.add_css_class('dim-label')
        artist_stack.add_named(artist_label, 'view')

        artist_entry = Gtk.Entry()
        artist_entry.set_text(artist)
        artist_entry.set_placeholder_text('Artist One; Artist Two')
        artist_entry.set_hexpand(True)
        artist_entry.add_css_class('ready2rip-track-entry')
        artist_entry.set_tooltip_text(
            f'Artist for track {number:02d}. '
            'Multiple artists: separate with a semicolon (e.g. A; B)'
        )
        artist_entry.connect(
            'changed',
            lambda *_a, n=number: self._on_track_field_changed(n),
        )
        artist_stack.add_named(artist_entry, 'edit')
        artist_stack.set_visible_child_name('view')
        self._track_artist_size.add_widget(artist_stack)
        box.append(artist_stack)

        status = Gtk.Label(label='')
        status.add_css_class('caption')
        status.add_css_class('dim-label')
        status.add_css_class('ready2rip-track-status')
        status.set_ellipsize(Pango.EllipsizeMode.END)
        status.set_max_width_chars(12)
        status.set_xalign(1.0)
        status.set_valign(Gtk.Align.CENTER)
        status.set_visible(False)
        box.append(status)

        dur = Gtk.Label(label=duration_label)
        dur.add_css_class('ready2rip-track-dur')
        dur.add_css_class('dim-label')
        dur.add_css_class('caption')
        dur.set_valign(Gtk.Align.CENTER)
        dur.set_xalign(1.0)
        dur.set_tooltip_text(f'Duration {duration_label}')
        box.append(dur)

        row.set_child(box)

        self._track_checks[number] = check
        self._track_title_entries[number] = title_entry
        self._track_artist_entries[number] = artist_entry
        self._track_title_labels[number] = title_label
        self._track_artist_labels[number] = artist_label
        self._track_status_labels[number] = status
        self._track_title_stacks[number] = title_stack
        self._track_artist_stacks[number] = artist_stack
        return row

    def _on_tracks_edit_toggled(self, *_args) -> None:
        self._tracks_editing = not self._tracks_editing
        if not self._tracks_editing:
            # Leaving edit mode: push entry text into model + labels.
            for num in list(self._track_title_entries):
                self._on_track_field_changed(num)
            self._sync_track_labels_from_entries()
        self._apply_tracks_edit_mode()

    def _apply_tracks_edit_mode(self) -> None:
        editing = self._tracks_editing
        if self._track_edit_button is not None:
            self._track_edit_button.set_label('Done' if editing else 'Edit')
            self._track_edit_button.set_tooltip_text(
                'Finish editing' if editing else 'Edit titles and artists'
            )
        if self._track_fill_button is not None:
            self._track_fill_button.set_sensitive(editing)

        title_stacks = getattr(self, '_track_title_stacks', {})
        artist_stacks = getattr(self, '_track_artist_stacks', {})
        child = 'edit' if editing else 'view'
        for num in self._track_title_entries:
            ts = title_stacks.get(num)
            as_ = artist_stacks.get(num)
            if ts is not None:
                ts.set_visible_child_name(child)
            if as_ is not None:
                as_.set_visible_child_name(child)

        if editing and self._track_title_entries:
            first = min(self._track_title_entries)
            self._track_title_entries[first].grab_focus()

        self.track_edit_group.set_description(None)

    def _sync_track_labels_from_entries(self) -> None:
        from ready2rip.tags.artists import normalize_artists

        for num, title_entry in self._track_title_entries.items():
            title = title_entry.get_text().strip()
            artist = normalize_artists(self._track_artist_entries[num].get_text())
            # Keep entry text canonical (A; B not A;B).
            if self._track_artist_entries[num].get_text().strip() != artist:
                self._suppress_meta_write = True
                try:
                    self._track_artist_entries[num].set_text(artist)
                finally:
                    self._suppress_meta_write = False
            tlab = self._track_title_labels[num]
            alab = self._track_artist_labels[num]
            tlab.set_label(title or f'Track {num:02d}')
            tlab.remove_css_class('dim-label')
            if not title:
                tlab.add_css_class('dim-label')
            alab.set_label(artist or '—')
            alab.remove_css_class('dim-label')
            if not artist:
                alab.add_css_class('dim-label')

    def _on_track_field_changed(self, track_number: int) -> None:
        if self._suppress_meta_write or self._disc is None:
            return
        if not self._tracks_editing:
            return
        album = self._ensure_album_tracks()
        title_entry = self._track_title_entries.get(track_number)
        artist_entry = self._track_artist_entries.get(track_number)
        from ready2rip.tags.artists import normalize_artists

        title = title_entry.get_text().strip() if title_entry else ''
        artist = (
            normalize_artists(artist_entry.get_text()) if artist_entry else ''
        )

        found = None
        for t in album.tracks:
            if t.number == track_number:
                found = t
                break
        if found is None:
            found = TrackMetadata(number=track_number)
            album.tracks.append(found)
            album.tracks.sort(key=lambda x: x.number)
        found.title = title
        found.artist = artist

        _mark_album_edited(album)
        self._schedule_cache_save()

    def _set_all_tracks(self, active: bool) -> None:
        for check in self._track_checks.values():
            check.set_active(active)

    def _fill_empty_track_artists(self) -> None:
        """Copy album artist (incl. multi ``A; B``) into empty track artists."""
        from ready2rip.tags.artists import normalize_artists

        if self._disc is None:
            return
        if not self._tracks_editing:
            self._tracks_editing = True
            self._apply_tracks_edit_mode()
        album = self._ensure_album_tracks()
        album_artist = normalize_artists(album.artist)
        if not album_artist:
            self._toast('Set album artist first')
            return
        filled = 0
        self._suppress_meta_write = True
        try:
            for t in album.tracks:
                if (t.artist or '').strip():
                    continue
                t.artist = album_artist
                entry = self._track_artist_entries.get(t.number)
                if entry is not None:
                    entry.set_text(album_artist)
                filled += 1
        finally:
            self._suppress_meta_write = False
        if filled:
            _mark_album_edited(album)
            self._sync_track_labels_from_entries()
            self._schedule_cache_save()
            self._toast(
                f'Filled artist on {filled} track{"s" if filled != 1 else ""}'
            )
        else:
            self._toast('All tracks already have an artist')

    def _apply_ar_results_to_rows(self, results) -> None:
        by_num = {r.track_number: r for r in results}
        for num, ar in by_num.items():
            label = self._track_status_labels.get(num)
            if label is None:
                continue
            msg = ar.message or ''
            label.set_text(msg)
            label.set_tooltip_text(msg)
            label.set_visible(bool(msg))

    # —— Metadata lookup ——

    def _start_metadata_lookup(self, *, interactive: bool) -> None:
        if self._disc is None or self._ids is None or self._looking_up:
            return

        settings = self.store.get()
        if not settings.use_musicbrainz and not settings.use_freedb:
            if interactive:
                self._toast('Enable MusicBrainz or FreeDB under Metadata options')
            return

        self._lookup_generation += 1
        generation = self._lookup_generation
        self._looking_up = True
        self._set_lookup_controls_sensitive(False)
        self.lookup_progress.set_visible(True)
        self.lookup_progress.pulse()
        # lookup progress bar handles status

        mb_id = self._ids.musicbrainz_discid
        freedb_id = self._ids.freedb_id
        use_mb = settings.use_musicbrainz
        use_fb = settings.use_freedb

        def worker() -> None:
            try:
                results = lookup_metadata(
                    mb_id,
                    freedb_id,
                    use_musicbrainz=use_mb,
                    use_freedb=use_fb,
                )
                error = None
            except Exception as exc:  # noqa: BLE001
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
        GLib.timeout_add(100, self._pulse_lookup_progress)

    def _start_mb_url_fetch(self, text: str) -> None:
        """Fetch a single release from a MusicBrainz link (background)."""
        if self._looking_up:
            return

        self._lookup_generation += 1
        generation = self._lookup_generation
        self._looking_up = True
        self._set_lookup_controls_sensitive(False)
        self.lookup_progress.set_visible(True)
        self.lookup_progress.pulse()
        # lookup progress bar handles status

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
            except Exception as exc:  # noqa: BLE001
                error = str(exc)

            def done() -> bool:
                self._on_mb_url_finished(generation, album, error)
                return GLib.SOURCE_REMOVE

            GLib.idle_add(done)

        threading.Thread(target=worker, daemon=True).start()
        GLib.timeout_add(100, self._pulse_lookup_progress)

    def _pulse_lookup_progress(self) -> bool:
        if not self._looking_up:
            return GLib.SOURCE_REMOVE
        self.lookup_progress.pulse()
        return GLib.SOURCE_CONTINUE

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
        self.lookup_progress.set_visible(False)
        self._set_lookup_controls_sensitive(
            self._disc is not None and not self._ripping
        )

        if error:
            # toast already covers lookup failure
            if interactive:
                self._toast(f'Lookup failed: {error}')
            return

        if not results:
            # toast already covers no matches
            if interactive:
                self._toast('No metadata matches for this disc')
            return

        if len(results) == 1 and not interactive:
            self._apply_album(results[0])
            self._toast(results[0].display_label)
            return

        if len(results) == 1:
            self._apply_album(results[0])
            self._toast(f'Using {results[0].source}: {results[0].title}')
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
        self.lookup_progress.set_visible(False)
        self._set_lookup_controls_sensitive(
            self._disc is not None and not self._ripping
        )

        if error or album is None:
            msg = error or 'Could not load that MusicBrainz release'
            # toast already covers MB failure
            self._toast(msg)
            return

        if (
            self._disc is not None
            and album.tracks
            and len(album.tracks) != self._disc.track_count
        ):
            self._toast(
                f'Loaded “{album.title}” ({len(album.tracks)} tracks on this '
                f'medium; disc has {self._disc.track_count})'
            )
        else:
            self._toast(f'Using MusicBrainz: {album.title}')

        self._apply_album(album)

    def _show_picker(self, candidates: list[AlbumMetadata]) -> None:
        dialog = MetadataPickerDialog(candidates)

        def on_closed(*_a) -> None:
            chosen = dialog.chosen
            if chosen is not None:
                self._apply_album(chosen)
                self._toast(f'Using {chosen.source}: {chosen.title}')
            elif self._album is None:
                self._toast(f'{len(candidates)} matches · none selected')

        dialog.connect('closed', on_closed)
        dialog.present(self)

    def _apply_album(self, album: AlbumMetadata) -> None:
        # Keep disc track count aligned if lookup omitted some.
        self._album = album
        if self._disc is not None:
            self._ensure_album_tracks()
        self._clear_artwork()
        self._fill_album_edit_fields(self._album)
        self._update_album_header(self._disc, self._album)
        selected = {
            num: check.get_active() for num, check in self._track_checks.items()
        }
        self._rebuild_track_list(self._disc)
        for num, was in selected.items():
            if num in self._track_checks:
                self._track_checks[num].set_active(was)
        self._save_metadata_cache()
        if (
            self.store.get().fetch_artwork
            and self._artwork_source_options().any_enabled
        ):
            self._start_artwork_fetch(self._album)

    # —— Artwork ——

    @Gtk.Template.Callback()
    def _on_search_art_clicked(self, *_args) -> None:
        """Search online for cover art."""
        album = self._album
        settings = self.store.get()
        if album is None:
            self._toast('Insert a disc or look up metadata first')
            return
        if not settings.fetch_artwork or not self._artwork_source_options().any_enabled:
            self._toast('Enable Download artwork and at least one source')
            return
        self._start_artwork_fetch(album)
        self._toast('Searching for artwork…')

    @Gtk.Template.Callback()
    def _on_choose_art_clicked(self, *_args) -> None:
        self._choose_local_artwork()

    @Gtk.Template.Callback()
    def _on_clear_art_clicked(self, *_args) -> None:
        if self._artwork is None:
            self._toast('No artwork to remove')
            return
        self._clear_artwork()
        if self._album is not None:
            self._album.cover_url = ''
        self._schedule_cache_save()
        self._toast('Artwork removed')

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
            self._show_artwork(image)
            album = self._ensure_album_tracks()
            album.cover_url = path
            self._toast(f'Local cover art {image.label}')
            self._schedule_cache_save()

        dialog.open(self, None, on_done)

    def _clear_artwork(self) -> None:
        """Clear cover art from the UI."""
        self._art_generation += 1
        self._artwork = None
        self._artwork_embed = None
        self._show_placeholder_cover()
        self._sync_cover_action_sensitivity()
        self._update_art_info()

    def _start_artwork_fetch(self, album: AlbumMetadata) -> None:
        settings = self.store.get()
        if not settings.fetch_artwork:
            return
        sources = self._artwork_source_options()
        if not sources.any_enabled:
            return
        self._art_generation += 1
        generation = self._art_generation
        self._update_art_info('Downloading…')

        def worker() -> None:
            try:
                image = ArtworkFetcher().fetch_best(album, sources=sources)
                error = None
            except Exception as exc:  # noqa: BLE001
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
            self._update_art_info('Download failed')
            self._toast(f'Artwork failed: {error}')
            return

        if image is None:
            self._update_art_info('No artwork found')
            return

        self._artwork = image
        self._prepare_embed_artwork()
        self._show_artwork(image)
        self._toast(f'Cover art {image.label}')
        self._schedule_cache_save()

    def _prepare_embed_artwork(self) -> None:
        if self._artwork is None:
            self._artwork_embed = None
            self._update_art_info()
            return
        max_edge = self.store.get().artwork_max_size
        self._artwork_embed = ArtworkFetcher().resize(self._artwork, max_edge)
        self._update_art_info()

    def _show_artwork(self, image: ArtworkImage) -> None:
        """Show *image* full-bleed in the 240×240 cover frame."""
        size = self._COVER_SIZE
        self.cover_picture.set_content_fit(Gtk.ContentFit.COVER)
        self.cover_picture.set_size_request(size, size)
        self.cover_overlay.set_size_request(size, size)
        self.cover_frame.set_size_request(size, size)

        if not apply_artwork_to_picture(
            self.cover_picture, image, edge=size
        ):
            self._show_placeholder_cover()
            self._sync_cover_action_sensitivity()
            self._update_art_info()
            return

        # Hide the dimmed music glyph once real art is loaded.
        self.cover_placeholder.set_visible(False)
        self.cover_picture.set_size_request(size, size)
        self.cover_overlay.set_size_request(size, size)
        self.cover_frame.set_size_request(size, size)
        self._sync_cover_action_sensitivity()
        self._update_art_info()

    def _toast(self, title: str, timeout: int = 3) -> None:
        """Show a transient bottom toast (Adwaita / GNOME HIG).

        Auto-dismisses after *timeout* seconds. Timeout is clamped so toasts
        never stick forever (``0`` in libadwaita means no auto-dismiss).
        """
        # GNOME short notifications are typically ~2–5 s.
        seconds = max(2, min(int(timeout), 5))
        toast = Adw.Toast(title=title)
        toast.set_timeout(seconds)
        try:
            toast.set_priority(Adw.ToastPriority.NORMAL)
        except (AttributeError, TypeError):
            pass
        self.toast_overlay.add_toast(toast)


def _mark_album_edited(album: AlbumMetadata) -> None:
    """Annotate metadata source after a manual edit."""
    source = album.source or ''
    if source in ('musicbrainz', 'freedb') and not source.endswith('+manual'):
        album.source = f'{source}+manual'
    elif not source:
        album.source = 'manual'
