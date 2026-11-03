# SPDX-License-Identifier: GPL-3.0-or-later
"""Main application window."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gio, GLib, GObject, Gtk, Pango

from ready2rip import config
from ready2rip.accuraterip import AccurateRipConfidence
from ready2rip.artwork.fetch import (
    ArtworkFetcher,
    ArtworkImage,
    ArtworkSourceOptions,
    apply_artwork_to_picture,
    blank_cover_texture,
)
from ready2rip.ctdb import CtdbConfidence
from ready2rip.disc.discid_util import DiscIdentifiers, identifiers_from_disc
from ready2rip.disc.drive_info import DriveInfo, probe_drive
from ready2rip.disc.drive_status import (
    DriveStatus,
    DriveTrayState,
    eject_drive,
    query_drive_status,
)
from ready2rip.disc.probe import DiscInfo, probe_disc
from ready2rip.icons import apply_icon
from ready2rip.metadata.cache import MetadataCache
from ready2rip.metadata.providers import (
    AlbumMetadata,
    TrackMetadata,
    fetch_album_from_musicbrainz_link,
    lookup_metadata,
    parse_musicbrainz_release_id,
)
from ready2rip.metadata_dialog import MetadataPickerDialog
from ready2rip.paths import (
    DEFAULT_ALBUM_FOLDER_TEMPLATE,
    DEFAULT_FILENAME_TEMPLATE,
    FILENAME_SCHEMES,
    FILENAME_TEMPLATE_HELP,
    FOLDER_SCHEMES,
    FOLDER_TEMPLATE_HELP,
    scheme_index,
    track_meta_for,
)
from ready2rip.rip.engine import RipEngine, RipJob, RipProgress, RipResult, RipState
from ready2rip.settings import (
    AAC_QUALITIES,
    ARTWORK_SIZES,
    ENCODERS,
    FLAC_LEVELS,
    MP3_QUALITIES,
    OPUS_BITRATES,
    SettingsStore,
    default_output_directory,
    normalize_aac_quality,
    normalize_mp3_quality,
    opus_bitrate_label,
)
from ready2rip.setup_dialog import DriveSetupDialog
from ready2rip.tags.artists import normalize_artists
from ready2rip.util import normalize_release_date, parse_disc_field, validate_device_path
from ready2rip.window_styles import banner_fill_css, install_window_styles

log = logging.getLogger(__name__)

_WINDOW_UI = str(Path(__file__).with_name('window.ui'))

_ADW_TEMPLATE_TYPES = (
    'ApplicationWindow',
    'ToolbarView',
    'HeaderBar',
    'WindowTitle',
    'ToastOverlay',
    'StatusPage',
    'Clamp',
    'PreferencesGroup',
    'ActionRow',
    'ButtonRow',
    'ComboRow',
    'EntryRow',
    'SpinRow',
    'SwitchRow',
)
_adw_types_ensured = False


def ensure_adw_template_types() -> None:
    """Register Adw widgets used by window.ui for GtkBuilder.

    AppImage / some PyGObject builds skip this until type_ensure runs;
    without it, templates fail with "Invalid object type 'AdwToolbarView'".
    Call after Adw.Application startup — not at import time.
    """
    global _adw_types_ensured
    if _adw_types_ensured:
        return
    Adw.init()
    for name in _ADW_TEMPLATE_TYPES:
        typ = getattr(Adw, name, None)
        if typ is not None:
            GObject.type_ensure(typ)
    _adw_types_ensured = True


_RIP_STATUS_FALLBACK = {
    RipState.REPLAYGAIN: 'Applying ReplayGain…',
    RipState.ENCODING: 'Encoding…',
    RipState.VERIFYING: 'Verifying…',
    RipState.PREPARING: 'Preparing…',
    RipState.RIPPING: 'Extracting…',
}


def _empty_tray_copy(state: DriveTrayState, device: str) -> tuple[str, str] | None:
    """Status-page title and description for an empty or missing drive."""
    if state is DriveTrayState.TRAY_OPEN:
        return (
            'Tray open',
            f'The drive tray is open on {device}. Insert a disc and close the tray.',
        )
    if state is DriveTrayState.NO_DISC:
        return (
            'No disc',
            f'Tray is closed but no disc is in {device}. Insert an audio CD.',
        )
    if state is DriveTrayState.MISSING:
        return (
            'Drive not found',
            f'Cannot open {device}. Check the optical device path in Settings.',
        )
    return None


@Gtk.Template(filename=_WINDOW_UI)
class Ready2RipWindow(Adw.ApplicationWindow):
    __gtype_name__ = 'Ready2RipWindow'

    toast_overlay = Gtk.Template.Child()
    window_overlay = Gtk.Template.Child()
    flash_veil = Gtk.Template.Child()
    toolbar_view = Gtk.Template.Child()
    window_title = Gtk.Template.Child()
    menu_button = Gtk.Template.Child()
    settings_button = Gtk.Template.Child()
    status_page = Gtk.Template.Child()
    stack = Gtk.Template.Child()
    track_edit_group = Gtk.Template.Child()
    album_edit_group = Gtk.Template.Child()
    rip_button = Gtk.Template.Child()
    lookup_button = Gtk.Template.Child()
    eject_button = Gtk.Template.Child()
    empty_eject_button = Gtk.Template.Child()
    art_album_row = Gtk.Template.Child()
    art_group = Gtk.Template.Child()
    art_column = Gtk.Template.Child()
    cover_frame = Gtk.Template.Child()
    cover_overlay = Gtk.Template.Child()
    cover_picture = Gtk.Template.Child()
    cover_placeholder = Gtk.Template.Child()
    cover_actions = Gtk.Template.Child()
    cover_size_label = Gtk.Template.Child()
    search_art_button = Gtk.Template.Child()
    choose_art_button = Gtk.Template.Child()
    clear_art_button = Gtk.Template.Child()
    lookup_progress = Gtk.Template.Child()
    rip_revealer = Gtk.Template.Child()
    rip_banner = Gtk.Template.Child()
    rip_title_label = Gtk.Template.Child()
    rip_status_label = Gtk.Template.Child()

    # Cover sits in the Artwork column (~1/3 of the disc hero row).
    _COVER_SIZE = 180
    _COVER_SIZE_MIN = 140
    _COVER_SIZE_MAX = 280
    _COVER_PLACEHOLDER_ICON_SIZE = 48

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
        self._track_artist_button: Gtk.Button | None = None
        self._track_select_all: Gtk.CheckButton | None = None
        self._syncing_select_all = False
        # Title and Artist columns share width across rows (0.4.11 layout).
        self._track_title_size = Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL)
        self._track_artist_size = Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL)
        self._album_field_rows: list[Gtk.Widget] = []
        self._album_title_row: Adw.EntryRow | None = None
        self._album_artist_row: Adw.EntryRow | None = None
        self._album_date_row: Adw.EntryRow | None = None
        self._album_genre_row: Adw.EntryRow | None = None
        self._album_label_row: Adw.EntryRow | None = None
        self._album_disc_row: Adw.EntryRow | None = None
        self._track_header_suffix: Gtk.Widget | None = None
        self._disc_rows: list[Adw.ActionRow] = []
        self._drive_rows: list[Gtk.Widget] = []
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
        self._poll_in_flight = False
        self._pending_progress: RipProgress | None = None
        self._progress_idle_id: int | None = None
        self._banner_shown = 0.0
        self._banner_target = 0.0
        self._banner_tick_id: int | None = None
        self._banner_last_us = 0
        self._banner_hold = False
        self._banner_provider = Gtk.CssProvider()
        self._flash_id: int | None = None
        self.store = store if store is not None else SettingsStore()
        self._device = self.store.get().device

        self._settings_dialog: Adw.Dialog | None = None
        # Preferences pages: Ripping / Encoding / Metadata / Drive
        self.extraction_group = Adw.PreferencesGroup(title='Extraction')
        self.automation_group = Adw.PreferencesGroup(title='Automation')
        self.encoding_group = Adw.PreferencesGroup(title='Encoding')
        self.files_group = Adw.PreferencesGroup(title='Files')
        self.device_group = Adw.PreferencesGroup(title='Device')
        self.lookup_group = Adw.PreferencesGroup(title='Lookup')
        self.artwork_group = Adw.PreferencesGroup(title='Artwork')
        self.tags_group = Adw.PreferencesGroup(title='Tags')
        self.disc_group = Adw.PreferencesGroup(title='Disc')
        self.drive_group = Adw.PreferencesGroup(title='Drive')
        self._flac_levels = [level for level, _label in FLAC_LEVELS]
        self._mp3_qualities = [qid for qid, _label in MP3_QUALITIES]
        self._aac_qualities = [qid for qid, _label in AAC_QUALITIES]
        self._opus_values = list(OPUS_BITRATES)
        self._quality_kind: str | None = None

        self._setup_window_actions()
        self._setup_header_icons()
        self._setup_lookup_menu()
        self._setup_settings_dialog()
        self._setup_cover_widget()
        self._setup_progress_styles()
        self._build_options_rows()
        self._build_metadata_rows()
        self._build_album_edit_rows()
        # Title the Tracks group early so the disc page is ready before a disc loads.
        self.track_edit_group.set_title('Tracks')
        self.connect('close-request', self._on_close_request)
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
        except Exception:
            pass
        # Album EntryRows must not open in edit mode on launch.
        self._clear_window_focus()
        self._start_async_probe(device, reason='startup')
        return GLib.SOURCE_REMOVE

    def _clear_window_focus(self) -> bool:
        """Drop keyboard focus so Adw.EntryRow does not look like it is editing."""
        self.set_focus(None)
        return GLib.SOURCE_REMOVE

    def _defer_clear_window_focus(self) -> None:
        GLib.idle_add(self._clear_window_focus)

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
                            f'{err or "Unknown error"}\n'
                            'Check Optical device path in Settings.'
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
                except Exception:
                    log.exception('Disc probe UI update failed (%s)', reason)
                    if reason == 'startup':
                        self._start_drive_monitor()
                return GLib.SOURCE_REMOVE

            GLib.idle_add(finish)

        threading.Thread(target=work, daemon=True, name=f'ready2rip-probe-{reason}').start()

    def _setup_window_actions(self) -> None:
        """Window actions used by the header bar and keyboard shortcuts."""
        for name, handler in (
            ('rip', self._on_rip_clicked),
            ('eject', self._on_eject_clicked),
            ('close', lambda *_: self.close()),
            ('preferences', self._on_preferences),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect('activate', handler)
            self.add_action(action)

        app = self.get_application()
        if app is not None and hasattr(app, 'build_primary_menu'):
            self.menu_button.set_menu_model(app.build_primary_menu())
        if hasattr(self.menu_button, 'set_primary'):
            self.menu_button.set_primary(True)
        self._set_action_enabled('rip', False)

    def _setup_header_icons(self) -> None:
        """Resolve symbolic icons against the active theme (with fallbacks)."""
        display = self.get_display()
        apply_icon(self.menu_button, 'help-about-symbolic', display=display)
        apply_icon(
            self.settings_button, 'preferences-system-symbolic', display=display
        )
        apply_icon(self.lookup_button, 'system-search-symbolic', display=display)
        apply_icon(self.eject_button, 'media-eject-symbolic', display=display)
        apply_icon(self.empty_eject_button, 'media-eject-symbolic', display=display)
        apply_icon(self.search_art_button, 'system-search-symbolic', display=display)
        apply_icon(self.choose_art_button, 'folder-symbolic', display=display)
        apply_icon(self.clear_art_button, 'user-trash-symbolic', display=display)
        apply_icon(self.status_page, 'media-optical-symbolic', display=display)
        apply_icon(self.cover_placeholder, 'folder-music-symbolic', display=display)

    def _on_close_request(self, *_args) -> bool:
        # Closing mid-rip used to kill the daemon thread abruptly and leave
        # a half-written log that looked like a crash. Cancel cleanly first.
        if self._ripping and self._rip_engine is not None:
            self._rip_engine.cancel()
        if self._flash_id is not None:
            GLib.source_remove(self._flash_id)
            self._flash_id = None
        if self._banner_tick_id is not None:
            self.remove_tick_callback(self._banner_tick_id)
            self._banner_tick_id = None
        return False

    def _setup_settings_dialog(self) -> None:
        """Settings dialog with category switcher in the header (top)."""
        pages: list[tuple[str, str, tuple[Adw.PreferencesGroup, ...]]] = [
            (
                'Ripping',
                'media-optical-symbolic',
                (self.extraction_group, self.automation_group),
            ),
            (
                'Encoding',
                'audio-x-generic-symbolic',
                (self.encoding_group, self.files_group),
            ),
            (
                'Metadata',
                'folder-music-symbolic',
                (self.lookup_group, self.artwork_group, self.tags_group),
            ),
            (
                'Drive',
                'drive-optical-symbolic',
                (self.device_group, self.disc_group, self.drive_group),
            ),
        ]

        dialog = Adw.Dialog(title='Settings')
        dialog.set_content_width(560)
        dialog.set_content_height(700)

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        stack = Adw.ViewStack()
        stack.set_vexpand(True)
        stack.set_hexpand(True)

        switcher = Adw.ViewSwitcher(stack=stack)
        policy = getattr(Adw, 'ViewSwitcherPolicy', None)
        if policy is not None and hasattr(switcher, 'set_policy'):
            # Prefer the wide (top) labels+icons layout when space allows.
            wide = getattr(policy, 'WIDE', None)
            if wide is not None:
                switcher.set_policy(wide)
        header.set_title_widget(switcher)
        toolbar.add_top_bar(header)

        for title, icon, groups in pages:
            page = Adw.PreferencesPage(title=title, icon_name=icon)
            for group in groups:
                page.add(group)
            scrolled = Gtk.ScrolledWindow(
                hscrollbar_policy=Gtk.PolicyType.NEVER,
                vexpand=True,
                hexpand=True,
            )
            scrolled.set_child(page)
            name = title.lower()
            if hasattr(stack, 'add_titled_with_icon'):
                stack.add_titled_with_icon(scrolled, name, title, icon)
            else:
                stack.add_titled(scrolled, name, title)

        toolbar.set_content(stack)
        dialog.set_child(toolbar)
        self._settings_dialog = dialog

    def _on_preferences(self, *_args) -> None:
        if self._settings_dialog is None:
            self._setup_settings_dialog()
        assert self._settings_dialog is not None
        self.sync_options_from_store()
        self._settings_dialog.present(self)

    def _setup_cover_widget(self) -> None:
        """Square cover in the Artwork 1/3 column; actions on hover."""
        self._cover_display_size = self._COVER_SIZE
        for widget in (self.cover_overlay, self.cover_picture):
            widget.set_hexpand(True)
            widget.set_vexpand(True)
        self.cover_frame.set_hexpand(False)
        self.cover_frame.set_vexpand(False)
        self.cover_frame.set_halign(Gtk.Align.CENTER)
        self._apply_cover_display_size(self._COVER_SIZE)

        self.cover_picture.set_content_fit(Gtk.ContentFit.FILL)
        if hasattr(self.cover_picture, 'set_can_shrink'):
            self.cover_picture.set_can_shrink(True)

        # Opacity on the actions box (+ buttons) — leave can_target on so
        # hover→click does not race with leave/hide (last working release).
        # Not focusable: avoids tab-focus leaving icons stuck visible.
        self._set_cover_actions_visible(False)
        for button in (
            self.search_art_button,
            self.choose_art_button,
            self.clear_art_button,
        ):
            button.set_focusable(False)
        self._show_placeholder_cover()
        self._sync_cover_action_sensitivity()

        motion = Gtk.EventControllerMotion()
        motion.connect('enter', self._on_cover_pointer_enter)
        motion.connect('leave', self._on_cover_pointer_leave)
        self.cover_frame.add_controller(motion)

        self._attach_art_album_grid()
        self.art_album_row.connect('notify::width', self._on_art_album_row_width)

    def _attach_art_album_grid(self) -> None:
        """Place Artwork|Album into a 1:2 column span on the hero grid."""
        grid = self.art_album_row
        art = self.art_group
        album = self.album_edit_group
        if art.get_parent() is grid:
            grid.remove(art)
        if album.get_parent() is grid:
            grid.remove(album)
        grid.set_column_homogeneous(True)
        grid.set_column_spacing(18)
        grid.attach(art, 0, 0, 1, 1)
        grid.attach(album, 1, 0, 2, 1)

    def _on_art_album_row_width(self, *_args) -> None:
        self._fit_cover_to_column()

    def _cover_size_for_row(self) -> int | None:
        """Square cover edge from the hero row's 1/3 column — not art_group width.

        Using art_group width fed back into size_request and grew without bound.
        """
        row_w = self.art_album_row.get_width()
        if row_w < self._COVER_SIZE_MIN * 2:
            return None
        spacing = self.art_album_row.get_column_spacing()
        # 3 homogeneous units → 2 gaps.
        col_w = max(1, (row_w - 2 * spacing) // 3)
        # Inset for PreferencesGroup / list padding inside the cell.
        size = col_w - 8
        return max(self._COVER_SIZE_MIN, min(self._COVER_SIZE_MAX, size))

    def _fit_cover_to_column(self) -> None:
        """Keep the cover square within the Artwork 1/3 column."""
        size = self._cover_size_for_row()
        if size is None:
            return
        if size == getattr(self, '_cover_display_size', None):
            return
        self._apply_cover_display_size(size)
        if self._artwork is not None:
            apply_artwork_to_picture(
                self.cover_picture, self._artwork, edge=size
            )
        else:
            self._apply_blank_cover_paintable(size)

    def _apply_cover_display_size(self, size: int) -> None:
        self._cover_display_size = size
        for widget in (self.cover_overlay, self.cover_picture, self.cover_frame):
            widget.set_size_request(size, size)
        icon_px = max(32, min(self._COVER_PLACEHOLDER_ICON_SIZE, size // 4))
        self.cover_placeholder.set_pixel_size(icon_px)
        install_window_styles(self.get_display(), cover_size=size)

    def _apply_blank_cover_paintable(self, size: int) -> None:
        """Hold the cover slot open at *size* when no artwork is loaded."""
        scale = 1
        try:
            scale = max(1, int(self.get_scale_factor()))
        except Exception:
            pass
        texture = blank_cover_texture(size, scale=scale)
        if texture is not None:
            self.cover_picture.set_paintable(texture)
        self.cover_picture.set_content_fit(Gtk.ContentFit.FILL)

    def _setup_progress_styles(self) -> None:
        """Install cover / track / progress CSS and wire the rip banner fill."""
        install_window_styles(
            self.get_display(),
            cover_size=getattr(self, '_cover_display_size', self._COVER_SIZE),
        )
        self.flash_veil.set_halign(Gtk.Align.FILL)
        self.flash_veil.set_valign(Gtk.Align.FILL)
        self.flash_veil.set_hexpand(True)
        self.flash_veil.set_vexpand(True)
        self.rip_banner.get_style_context().add_provider(
            self._banner_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 20,
        )
        self._apply_banner_fill()

    def _set_cover_actions_visible(self, visible: bool) -> None:
        # Opacity only — leave can_target enabled so moving onto the buttons
        # does not race with leave/hide before click. Also set each button so
        # OSD styling cannot leave icons stuck visible/invisible.
        opacity = 1.0 if visible else 0.0
        self.cover_actions.set_opacity(opacity)
        for button in (
            self.search_art_button,
            self.choose_art_button,
            self.clear_art_button,
        ):
            button.set_opacity(opacity)

    def _on_cover_pointer_enter(self, *_args) -> None:
        self._set_cover_actions_visible(True)

    def _on_cover_pointer_leave(self, *_args) -> None:
        self._set_cover_actions_visible(False)

    def _sync_cover_action_sensitivity(self) -> None:
        self.clear_art_button.set_sensitive(self._artwork is not None)

    def _set_cover_frame_empty(self, empty: bool) -> None:
        if empty:
            self.cover_frame.add_css_class('ready2rip-cover-empty')
        else:
            self.cover_frame.remove_css_class('ready2rip-cover-empty')

    def _show_placeholder_cover(self) -> None:
        """Show the music icon in a cover slot the same size as real artwork."""
        size = getattr(self, '_cover_display_size', self._COVER_SIZE)
        self._apply_cover_display_size(size)
        self._apply_blank_cover_paintable(size)

        apply_icon(
            self.cover_placeholder, 'folder-music-symbolic', display=self.get_display()
        )
        self.cover_placeholder.add_css_class('ready2rip-cover-placeholder')
        self.cover_placeholder.set_visible(True)
        self.cover_placeholder.set_opacity(1.0)
        self._set_cover_frame_empty(True)
        self._update_cover_size_label()
        self._fit_cover_to_column()

    def _update_cover_size_label(self) -> None:
        """Show source (and embed) dimensions under the cover when art is loaded."""
        art = self._artwork
        if art is None:
            self.cover_size_label.set_visible(False)
            self.cover_size_label.set_text('')
            return

        lines = [f'{art.width}×{art.height}']
        if self.store.get().embed_artwork:
            emb = self._artwork_embed
            if emb is None:
                self._prepare_embed_artwork()
                emb = self._artwork_embed
            if emb is not None:
                lines.append(f'Embed {emb.width}×{emb.height}')
        self.cover_size_label.set_text('\n'.join(lines))
        self.cover_size_label.set_visible(True)

    # —— Settings groups (Preferences dialog) ——

    def _build_calibration_row(self) -> None:
        """Detect row on the Drive page; Sample offset nests inside the expander."""
        settings = self.store.get()
        self._setup_row = Adw.ExpanderRow(title='Detect sample offset')
        self._setup_row.set_tooltip_text(
            'Measure the AccurateRip sample offset for this drive'
        )

        # Status checkmark when calibrated (GNOME: symbolic + success).
        self._setup_ok_icon = Gtk.Image()
        apply_icon(self._setup_ok_icon, 'emblem-ok-symbolic', display=self.get_display())
        self._setup_ok_icon.set_valign(Gtk.Align.CENTER)
        self._setup_ok_icon.add_css_class('success')
        self._setup_ok_icon.set_visible(False)
        self._setup_row.add_suffix(self._setup_ok_icon)

        # Action: text button, or compact grey optical icon when complete.
        self._setup_btn = Gtk.Button(label='Detect')
        self._setup_btn.set_valign(Gtk.Align.CENTER)
        self._setup_btn.connect('clicked', self._on_run_drive_setup)
        self._setup_row.add_suffix(self._setup_btn)

        status = (
            f'Configured for {settings.drive_offset_device or "this drive"}'
            if settings.drive_offset_configured
            else 'Not calibrated yet'
        )
        self._offset_row = Adw.SpinRow(
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
        self._offset_row.set_tooltip_text(status)
        self._offset_row.connect('changed', self._on_offset_changed)
        self._setup_row.add_row(self._offset_row)

        self.device_group.add(self._setup_row)
        self._update_calibration_row()

    def _update_calibration_row(self) -> None:
        """Refresh detect expander: title, check, and detect button style."""
        settings = self.store.get()
        done = bool(settings.drive_offset_configured)
        btn = self._setup_btn
        row = self._setup_row

        for cls in ('suggested-action', 'flat', 'circular', 'ready2rip-setup-icon'):
            btn.remove_css_class(cls)

        if done:
            device = settings.drive_offset_device or settings.device or 'this drive'
            offset = settings.drive_sample_offset
            row.set_title('Sample offset detected')
            row.set_subtitle(f'{offset:+d} samples')
            row.set_tooltip_text(f'{offset:+d} samples · {device}')
            self._setup_ok_icon.set_visible(True)
            apply_icon(btn, 'media-optical-symbolic', display=self.get_display())
            btn.add_css_class('flat')
            btn.add_css_class('circular')
            btn.add_css_class('ready2rip-setup-icon')
            btn.set_tooltip_text('Detect sample offset again')
            self._offset_row.set_tooltip_text(f'Configured for {device}')
        else:
            row.set_title('Detect sample offset')
            row.set_subtitle('')
            row.set_tooltip_text(
                'Measure the AccurateRip sample offset for this drive'
            )
            self._setup_ok_icon.set_visible(False)
            btn.set_icon_name('')
            btn.set_label('Detect')
            btn.add_css_class('suggested-action')
            btn.set_tooltip_text('Detect sample offset for this drive')
            self._offset_row.set_tooltip_text('Not calibrated yet')

    def _add_option_row(
        self,
        row: Gtk.Widget,
        group: Adw.PreferencesGroup | None = None,
    ) -> None:
        (group or self.extraction_group).add(row)

    def _add_metadata_row(
        self,
        row: Gtk.Widget,
        group: Adw.PreferencesGroup | None = None,
    ) -> None:
        (group or self.lookup_group).add(row)

    def _build_options_rows(self) -> None:
        settings = self.store.get()
        default_out = default_output_directory()
        ids = [e[0] for e in ENCODERS]

        # —— Drive page: Device → detect offset (Sample offset nested) ——
        self._device_row = Adw.EntryRow(title='Device')
        self._device_row.set_text(settings.device or '/dev/sr0')
        self._device_row.set_tooltip_text('Optical device, for example /dev/sr0')
        self._device_row.connect('changed', self._on_device_path_changed)
        self._add_option_row(self._device_row, self.device_group)
        self._build_calibration_row()

        # —— Encoding page: format + quality ——
        self._encoder_row = Adw.ComboRow(title='Format')
        self._encoder_row.set_model(
            Gtk.StringList.new([label for _id, label in ENCODERS])
        )
        try:
            self._encoder_row.set_selected(ids.index(settings.encode_format))
        except ValueError:
            self._encoder_row.set_selected(0)
        self._encoder_row.connect('notify::selected', self._on_encoder_changed)
        self._add_option_row(self._encoder_row, self.encoding_group)

        self._quality_row = Adw.ComboRow(title='Quality')
        self._quality_row.connect('notify::selected', self._on_quality_changed)
        self._add_option_row(self._quality_row, self.encoding_group)
        self._sync_encoding_ui()

        # —— Encoding page: files ——
        self._output_row = Adw.EntryRow(title='Output folder')
        self._output_row.set_text(settings.output_directory or default_out)
        self._output_row.set_tooltip_text(default_out)
        self._output_row.connect('changed', self._on_output_changed)
        folder_btn = Gtk.Button()
        apply_icon(folder_btn, 'folder-open-symbolic', display=self.get_display())
        folder_btn.add_css_class('flat')
        folder_btn.set_valign(Gtk.Align.CENTER)
        folder_btn.set_tooltip_text('Choose folder')
        folder_btn.connect('clicked', self._on_choose_output_folder)
        self._output_row.add_suffix(folder_btn)
        self._add_option_row(self._output_row, self.files_group)

        self._folder_scheme_row = Adw.ComboRow(title='Album folder scheme')
        self._folder_scheme_row.set_model(
            Gtk.StringList.new([s.label for s in FOLDER_SCHEMES])
        )
        self._folder_scheme_row.set_selected(
            scheme_index(FOLDER_SCHEMES, settings.album_folder_template)
        )
        self._folder_scheme_row.set_tooltip_text(
            'Folder layout under the output folder. Custom unlocks the template.'
        )
        self._folder_scheme_row.connect(
            'notify::selected', self._on_folder_scheme_changed
        )
        self._add_option_row(self._folder_scheme_row, self.files_group)

        self._folder_template_row = Adw.EntryRow(title='Album folder template')
        self._folder_template_row.set_text(settings.album_folder_template)
        self._folder_template_row.set_tooltip_text(FOLDER_TEMPLATE_HELP)
        self._folder_template_row.connect(
            'changed', self._on_folder_template_changed
        )
        self._add_option_row(self._folder_template_row, self.files_group)

        self._filename_scheme_row = Adw.ComboRow(title='Track filename scheme')
        self._filename_scheme_row.set_model(
            Gtk.StringList.new([s.label for s in FILENAME_SCHEMES])
        )
        self._filename_scheme_row.set_selected(
            scheme_index(FILENAME_SCHEMES, settings.filename_template)
        )
        self._filename_scheme_row.set_tooltip_text(
            'Track file naming. Custom unlocks the template.'
        )
        self._filename_scheme_row.connect(
            'notify::selected', self._on_filename_scheme_changed
        )
        self._add_option_row(self._filename_scheme_row, self.files_group)

        self._filename_row = Adw.EntryRow(title='Track filename template')
        self._filename_row.set_text(settings.filename_template)
        self._filename_row.set_tooltip_text(FILENAME_TEMPLATE_HELP)
        self._filename_row.connect('changed', self._on_filename_changed)
        self._add_option_row(self._filename_row, self.files_group)
        self._sync_naming_template_sensitivity()

        # —— Ripping page: extraction ——
        self._test_copy_row = Adw.SwitchRow(
            title='Test and copy',
            active=settings.test_and_copy,
        )
        self._test_copy_row.set_tooltip_text('Rip twice and require matching CRCs')
        self._test_copy_row.connect('notify::active', self._on_test_copy_toggled)
        self._add_option_row(self._test_copy_row, self.extraction_group)

        self._copy_image_row = Adw.SwitchRow(
            title='Copy image',
            active=settings.copy_image,
        )
        self._copy_image_row.set_tooltip_text(
            'One continuous image file instead of per-track files'
        )
        self._copy_image_row.connect('notify::active', self._on_copy_image_toggled)
        self._add_option_row(self._copy_image_row, self.extraction_group)

        self._htoa_row = Adw.SwitchRow(
            title='Hidden track (HTOA)',
            active=settings.rip_htoa,
        )
        self._htoa_row.set_tooltip_text('Save non-silent audio before track 1')
        self._htoa_row.connect('notify::active', self._on_htoa_toggled)
        self._add_option_row(self._htoa_row, self.extraction_group)

        self._data_tracks_row = Adw.SwitchRow(
            title='Data tracks',
            active=settings.rip_data_tracks,
        )
        self._data_tracks_row.set_tooltip_text(
            'Rip mixed-mode and CD-Extra data as raw .bin files'
        )
        self._data_tracks_row.connect('notify::active', self._on_data_tracks_toggled)
        self._add_option_row(self._data_tracks_row, self.extraction_group)

        self._ar_row = Adw.SwitchRow(
            title='AccurateRip',
            active=settings.verify_accuraterip,
        )
        self._ar_row.set_tooltip_text('Verify against the AccurateRip database')
        self._ar_row.connect('notify::active', self._on_ar_toggled)
        self._add_option_row(self._ar_row, self.extraction_group)

        self._ctdb_row = Adw.SwitchRow(
            title='CTDB',
            active=settings.verify_ctdb,
        )
        self._ctdb_row.set_tooltip_text('Verify against the CUETools Database')
        self._ctdb_row.connect('notify::active', self._on_ctdb_toggled)
        self._add_option_row(self._ctdb_row, self.extraction_group)

        self._submit_ctdb_row = Adw.SwitchRow(
            title='Submit to CTDB',
            active=settings.submit_ctdb,
        )
        self._submit_ctdb_row.set_tooltip_text(
            'Confirm matches, or submit new parity for secure high-quality rips'
        )
        self._submit_ctdb_row.connect('notify::active', self._on_submit_ctdb_toggled)
        self._add_option_row(self._submit_ctdb_row, self.extraction_group)

        self._burst_row = Adw.SwitchRow(
            title='Burst fallback',
            active=settings.burst_fallback,
        )
        self._burst_row.set_tooltip_text(
            'Allow insecure -Z recovery after secure retries fail'
        )
        self._burst_row.connect('notify::active', self._on_burst_toggled)
        self._add_option_row(self._burst_row, self.extraction_group)

        self._log_row = Adw.SwitchRow(
            title='Write log',
            active=settings.write_rip_log,
        )
        self._log_row.set_tooltip_text('Save a detailed rip log in the album folder')
        self._log_row.connect('notify::active', self._on_log_toggled)
        self._add_option_row(self._log_row, self.extraction_group)

        self._cue_row = Adw.SwitchRow(
            title='Write CUE sheet',
            active=settings.write_cue_file,
        )
        self._cue_row.set_tooltip_text('Create a .cue file for the rip')
        self._cue_row.connect('notify::active', self._on_cue_toggled)
        self._add_option_row(self._cue_row, self.extraction_group)

        # —— Ripping page: automation ——
        self._auto_rip_row = Adw.SwitchRow(
            title='Auto-rip',
            active=settings.auto_rip,
        )
        self._auto_rip_row.set_tooltip_text(
            'Start ripping when an audio CD is detected'
        )
        self._auto_rip_row.connect('notify::active', self._on_auto_rip_toggled)
        self._add_option_row(self._auto_rip_row, self.automation_group)

        self._auto_eject_row = Adw.SwitchRow(
            title='Auto-eject',
            active=settings.auto_eject,
        )
        self._auto_eject_row.set_tooltip_text('Open the tray after a successful rip')
        self._auto_eject_row.connect('notify::active', self._on_auto_eject_toggled)
        self._add_option_row(self._auto_eject_row, self.automation_group)

    def _build_metadata_rows(self) -> None:
        settings = self.store.get()

        self._auto_lookup_row = Adw.ExpanderRow(title='Look up automatically')
        self._auto_lookup_row.set_tooltip_text(
            'Fetch metadata when a disc is detected'
        )
        self._auto_lookup_row.set_show_enable_switch(True)
        self._auto_lookup_row.set_enable_expansion(settings.auto_lookup_metadata)
        self._auto_lookup_row.set_expanded(settings.auto_lookup_metadata)
        self._auto_lookup_row.connect(
            'notify::enable-expansion', self._on_auto_lookup_toggled
        )

        self._mb_row = Adw.SwitchRow(
            title='MusicBrainz',
            active=settings.use_musicbrainz,
        )
        self._mb_row.set_tooltip_text('Preferred album and track metadata')
        self._mb_row.connect('notify::active', self._on_mb_toggled)
        self._auto_lookup_row.add_row(self._mb_row)

        self._freedb_row = Adw.SwitchRow(
            title='gnudb',
            active=settings.use_freedb,
        )
        self._freedb_row.set_tooltip_text('FreeDB-compatible disc lookup')
        self._freedb_row.connect('notify::active', self._on_freedb_toggled)
        self._auto_lookup_row.add_row(self._freedb_row)

        self._add_metadata_row(self._auto_lookup_row, self.lookup_group)
        self._sync_lookup_source_sensitivity(settings.auto_lookup_metadata)

        self._fetch_art_row = Adw.ExpanderRow(title='Download artwork')
        self._fetch_art_row.set_tooltip_text(
            'Fetch the highest-quality cover available'
        )
        self._fetch_art_row.set_show_enable_switch(True)
        self._fetch_art_row.set_enable_expansion(settings.fetch_artwork)
        self._fetch_art_row.set_expanded(settings.fetch_artwork)
        self._fetch_art_row.connect(
            'notify::enable-expansion', self._on_fetch_art_toggled
        )

        self._art_caa_row = Adw.SwitchRow(
            title='Cover Art Archive',
            active=settings.artwork_source_caa,
        )
        self._art_caa_row.set_tooltip_text('MusicBrainz release artwork')
        self._art_caa_row.connect('notify::active', self._on_art_caa_toggled)
        self._fetch_art_row.add_row(self._art_caa_row)

        self._art_itunes_row = Adw.SwitchRow(
            title='iTunes',
            active=settings.artwork_source_itunes,
        )
        self._art_itunes_row.set_tooltip_text('Apple Music store artwork')
        self._art_itunes_row.connect('notify::active', self._on_art_itunes_toggled)
        self._fetch_art_row.add_row(self._art_itunes_row)

        self._add_metadata_row(self._fetch_art_row, self.artwork_group)

        self._embed_art_row = Adw.SwitchRow(
            title='Embed artwork',
            active=settings.embed_artwork,
        )
        self._embed_art_row.set_tooltip_text('Write the cover into ripped files')
        self._embed_art_row.connect('notify::active', self._on_embed_art_toggled)
        self._add_metadata_row(self._embed_art_row, self.artwork_group)

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
        self._add_metadata_row(self._art_size_row, self.artwork_group)
        self._sync_artwork_source_sensitivity(settings.fetch_artwork)

        self._rg_row = Adw.SwitchRow(
            title='ReplayGain',
            active=settings.apply_replaygain,
        )
        self._rg_row.set_tooltip_text('Write track and album loudness tags')
        self._rg_row.connect('notify::active', self._on_rg_toggled)
        self._add_metadata_row(self._rg_row, self.tags_group)

    def sync_options_from_store(self) -> None:
        """Refresh settings widgets from store (e.g. after Drive setup)."""
        settings = self.store.get()
        ids = [e[0] for e in ENCODERS]
        default_out = default_output_directory()

        handlers = [
            (self._output_row, self._on_output_changed),
            (self._device_row, self._on_device_path_changed),
            (self._folder_scheme_row, self._on_folder_scheme_changed),
            (self._folder_template_row, self._on_folder_template_changed),
            (self._filename_scheme_row, self._on_filename_scheme_changed),
            (self._filename_row, self._on_filename_changed),
            (self._encoder_row, self._on_encoder_changed),
            (self._quality_row, self._on_quality_changed),
            (self._offset_row, self._on_offset_changed),
            (self._test_copy_row, self._on_test_copy_toggled),
            (self._htoa_row, self._on_htoa_toggled),
            (self._data_tracks_row, self._on_data_tracks_toggled),
            (self._copy_image_row, self._on_copy_image_toggled),
            (self._ar_row, self._on_ar_toggled),
            (self._ctdb_row, self._on_ctdb_toggled),
            (self._submit_ctdb_row, self._on_submit_ctdb_toggled),
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
            (self._art_size_row, self._on_art_size_changed),
        ]
        for widget, handler in handlers:
            widget.handler_block_by_func(handler)
        try:
            self._output_row.set_text(settings.output_directory or default_out)
            self._device_row.set_text(settings.device or '/dev/sr0')
            self._folder_template_row.set_text(settings.album_folder_template)
            self._folder_scheme_row.set_selected(
                scheme_index(FOLDER_SCHEMES, settings.album_folder_template)
            )
            self._filename_row.set_text(settings.filename_template)
            self._filename_scheme_row.set_selected(
                scheme_index(FILENAME_SCHEMES, settings.filename_template)
            )
            self._sync_naming_template_sensitivity()
            try:
                self._encoder_row.set_selected(ids.index(settings.encode_format))
            except ValueError:
                pass
            # Force quality combo to reload from store values.
            self._quality_kind = None
            self._sync_encoding_ui()
            self._offset_row.set_value(settings.drive_sample_offset)
            self._update_calibration_row()
            self._test_copy_row.set_active(settings.test_and_copy)
            self._htoa_row.set_active(settings.rip_htoa)
            self._data_tracks_row.set_active(settings.rip_data_tracks)
            self._copy_image_row.set_active(settings.copy_image)
            self._ar_row.set_active(settings.verify_accuraterip)
            self._ctdb_row.set_active(settings.verify_ctdb)
            self._submit_ctdb_row.set_active(settings.submit_ctdb)
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

    def _primary_encode_format(self) -> str:
        idx = self._encoder_row.get_selected()
        if 0 <= idx < len(ENCODERS):
            return ENCODERS[idx][0]
        return (self.store.get().encode_format or 'flac').strip().lower()

    def _quality_model_for(self, fmt: str) -> tuple[list[str], int]:
        """Return (labels, selected_index) for a codec quality combo."""
        settings = self.store.get()
        if fmt == 'flac':
            labels = [label for _level, label in FLAC_LEVELS]
            try:
                selected = self._flac_levels.index(settings.flac_compression)
            except ValueError:
                selected = 5
            return labels, selected
        if fmt == 'mp3':
            labels = [label for _qid, label in MP3_QUALITIES]
            try:
                selected = self._mp3_qualities.index(
                    normalize_mp3_quality(settings.mp3_quality)
                )
            except ValueError:
                selected = 0
            return labels, selected
        if fmt == 'aac':
            labels = [label for _qid, label in AAC_QUALITIES]
            try:
                selected = self._aac_qualities.index(
                    normalize_aac_quality(settings.aac_quality)
                )
            except ValueError:
                selected = self._aac_qualities.index('cbr256')
            return labels, selected
        if fmt == 'opus':
            labels = [opus_bitrate_label(r) for r in self._opus_values]
            try:
                selected = self._opus_values.index(settings.opus_bitrate)
            except ValueError:
                nearest = min(
                    self._opus_values,
                    key=lambda r: abs(r - settings.opus_bitrate),
                )
                selected = self._opus_values.index(nearest)
            return labels, selected
        return [], 0

    def _fill_quality_combo(
        self,
        row: Adw.ComboRow,
        fmt: str,
        *,
        kind_attr: str,
        handler,
    ) -> None:
        if fmt in ('', 'wav'):
            row.set_visible(False)
            setattr(self, kind_attr, None)
            return
        row.set_visible(True)
        current_kind = getattr(self, kind_attr)
        labels, selected = self._quality_model_for(fmt)
        row.handler_block_by_func(handler)
        try:
            if current_kind != fmt:
                row.set_model(Gtk.StringList.new(labels))
                setattr(self, kind_attr, fmt)
            row.set_selected(selected)
        finally:
            row.handler_unblock_by_func(handler)

    def _sync_encoding_ui(self) -> None:
        self._fill_quality_combo(
            self._quality_row,
            self._primary_encode_format(),
            kind_attr='_quality_kind',
            handler=self._on_quality_changed,
        )

    def _apply_quality_selection(self, fmt: str, index: int) -> None:
        if fmt == 'flac' and 0 <= index < len(self._flac_levels):
            self.store.update(flac_compression=self._flac_levels[index])
        elif fmt == 'mp3' and 0 <= index < len(self._mp3_qualities):
            self.store.update(mp3_quality=self._mp3_qualities[index])
        elif fmt == 'aac' and 0 <= index < len(self._aac_qualities):
            self.store.update(aac_quality=self._aac_qualities[index])
        elif fmt == 'opus' and 0 <= index < len(self._opus_values):
            self.store.update(opus_bitrate=self._opus_values[index])

    def _choose_folder(
        self,
        title: str,
        current: str,
        on_path: Callable[[str], None],
    ) -> None:
        dialog = Gtk.FileDialog(title=title)
        if hasattr(dialog, 'set_initial_folder'):
            try:
                dialog.set_initial_folder(Gio.File.new_for_path(current))
            except GLib.Error:
                pass

        def on_done(dlg: Gtk.FileDialog, result: Gio.AsyncResult) -> None:
            try:
                folder = dlg.select_folder_finish(result)
            except GLib.Error:
                return
            if folder is None:
                return
            path = folder.get_path()
            if not path:
                return
            on_path(path)

        dialog.select_folder(self, None, on_done)

    def _on_output_changed(self, row: Adw.EntryRow) -> None:
        text = row.get_text().strip()
        if not text:
            text = default_output_directory()
            row.set_text(text)
        self.store.update(output_directory=text)

    def _on_choose_output_folder(self, *_args) -> None:
        current = self._output_row.get_text().strip() or default_output_directory()

        def on_path(path: str) -> None:
            self._output_row.set_text(path)
            self.store.update(output_directory=path)

        self._choose_folder('Output folder', current, on_path)

    def _on_device_path_changed(self, row: Adw.EntryRow) -> None:
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

    def _sync_naming_template_sensitivity(self) -> None:
        """Template entries are editable only while Scheme is Custom."""
        folder_idx = self._folder_scheme_row.get_selected()
        folder_custom = (
            0 <= folder_idx < len(FOLDER_SCHEMES)
            and FOLDER_SCHEMES[folder_idx].id == 'custom'
        )
        self._folder_template_row.set_sensitive(folder_custom)

        file_idx = self._filename_scheme_row.get_selected()
        file_custom = (
            0 <= file_idx < len(FILENAME_SCHEMES)
            and FILENAME_SCHEMES[file_idx].id == 'custom'
        )
        self._filename_row.set_sensitive(file_custom)

    def _on_folder_scheme_changed(self, row: Adw.ComboRow, *_args) -> None:
        idx = row.get_selected()
        if not (0 <= idx < len(FOLDER_SCHEMES)):
            return
        scheme = FOLDER_SCHEMES[idx]
        if scheme.id != 'custom' and scheme.template:
            if self._folder_template_row.get_text().strip() != scheme.template:
                self._folder_template_row.handler_block_by_func(
                    self._on_folder_template_changed
                )
                try:
                    self._folder_template_row.set_text(scheme.template)
                finally:
                    self._folder_template_row.handler_unblock_by_func(
                        self._on_folder_template_changed
                    )
                self.store.update(album_folder_template=scheme.template)
        self._sync_naming_template_sensitivity()

    def _on_folder_template_changed(self, row: Adw.EntryRow) -> None:
        text = row.get_text().strip() or DEFAULT_ALBUM_FOLDER_TEMPLATE
        self.store.update(album_folder_template=text)
        idx = scheme_index(FOLDER_SCHEMES, text)
        if self._folder_scheme_row.get_selected() != idx:
            self._folder_scheme_row.handler_block_by_func(
                self._on_folder_scheme_changed
            )
            try:
                self._folder_scheme_row.set_selected(idx)
            finally:
                self._folder_scheme_row.handler_unblock_by_func(
                    self._on_folder_scheme_changed
                )
        self._sync_naming_template_sensitivity()

    def _on_filename_scheme_changed(self, row: Adw.ComboRow, *_args) -> None:
        idx = row.get_selected()
        if not (0 <= idx < len(FILENAME_SCHEMES)):
            return
        scheme = FILENAME_SCHEMES[idx]
        if scheme.id != 'custom' and scheme.template:
            if self._filename_row.get_text().strip() != scheme.template:
                self._filename_row.handler_block_by_func(self._on_filename_changed)
                try:
                    self._filename_row.set_text(scheme.template)
                finally:
                    self._filename_row.handler_unblock_by_func(
                        self._on_filename_changed
                    )
                self.store.update(filename_template=scheme.template)
        self._sync_naming_template_sensitivity()

    def _on_filename_changed(self, row: Adw.EntryRow) -> None:
        text = row.get_text().strip() or DEFAULT_FILENAME_TEMPLATE
        self.store.update(filename_template=text)
        idx = scheme_index(FILENAME_SCHEMES, text)
        if self._filename_scheme_row.get_selected() != idx:
            self._filename_scheme_row.handler_block_by_func(
                self._on_filename_scheme_changed
            )
            try:
                self._filename_scheme_row.set_selected(idx)
            finally:
                self._filename_scheme_row.handler_unblock_by_func(
                    self._on_filename_scheme_changed
                )
        self._sync_naming_template_sensitivity()

    def _on_encoder_changed(self, row: Adw.ComboRow, *_args) -> None:
        idx = row.get_selected()
        if 0 <= idx < len(ENCODERS):
            fmt = ENCODERS[idx][0]
            self.store.update(encode_format=fmt)
            self._sync_encoding_ui()

    def _on_quality_changed(self, row: Adw.ComboRow, *_args) -> None:
        fmt = self._quality_kind or self._primary_encode_format()
        self._apply_quality_selection(fmt, row.get_selected())

    def _on_offset_changed(self, row: Adw.SpinRow) -> None:
        device = self.store.get().device or self._device or '/dev/sr0'
        self.store.update(
            drive_sample_offset=int(row.get_value()),
            drive_offset_configured=True,
            drive_offset_device=device,
        )
        try:
            Gio.Settings.sync()
        except Exception:
            pass
        self._update_calibration_row()

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

    def _on_data_tracks_toggled(self, row: Adw.SwitchRow, *_args) -> None:
        self.store.update(rip_data_tracks=row.get_active())

    def _on_copy_image_toggled(self, row: Adw.SwitchRow, *_args) -> None:
        self.store.update(copy_image=row.get_active())
        if row.get_active():
            self._toast('Copy image on — rip will write one continuous disc image')

    def _on_rg_toggled(self, row: Adw.SwitchRow, *_args) -> None:
        self.store.update(apply_replaygain=row.get_active())

    def _on_ar_toggled(self, row: Adw.SwitchRow, *_args) -> None:
        self.store.update(verify_accuraterip=row.get_active())

    def _on_ctdb_toggled(self, row: Adw.SwitchRow, *_args) -> None:
        self.store.update(verify_ctdb=row.get_active())

    def _on_submit_ctdb_toggled(self, row: Adw.SwitchRow, *_args) -> None:
        self.store.update(submit_ctdb=row.get_active())

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
        if enabled and self._artwork is not None:
            self._prepare_embed_artwork()
        self._update_cover_size_label()

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

    def _sync_artwork_source_sensitivity(self, fetch_enabled: bool) -> None:
        for row in (self._art_caa_row, self._art_itunes_row):
            row.set_sensitive(fetch_enabled)

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

    def _on_art_size_changed(self, row: Adw.ComboRow, *_args) -> None:
        idx = row.get_selected()
        if 0 <= idx < len(self._art_size_values):
            self.store.update(artwork_max_size=self._art_size_values[idx])
            if self._artwork is not None:
                self._prepare_embed_artwork()
                self._update_cover_size_label()
                emb = self._artwork_embed
                if emb is not None:
                    self._toast(
                        f'Embed size set to {emb.width}×{emb.height} '
                        f'(from {self._artwork.width}×{self._artwork.height} source)'
                    )

    # —— Header actions ——

    def _on_eject_clicked(self, *_args) -> None:
        if self._ripping:
            self._toast('Cannot eject while ripping')
            return
        self._eject_disc()

    def _setup_lookup_menu(self) -> None:
        """Lookup menu: disc ID providers, gnudb, or a MusicBrainz release link."""
        disc_action = Gio.SimpleAction.new('lookup-disc', None)
        disc_action.connect('activate', self._on_lookup_disc)
        self.add_action(disc_action)

        gnudb_action = Gio.SimpleAction.new('lookup-gnudb', None)
        gnudb_action.connect('activate', self._on_lookup_gnudb)
        self.add_action(gnudb_action)

        mb_action = Gio.SimpleAction.new('lookup-mb-url', None)
        mb_action.connect('activate', self._on_lookup_mb_url)
        self.add_action(mb_action)

        menu = Gio.Menu()
        section = Gio.Menu()
        section.append('By disc ID', 'win.lookup-disc')
        section.append('From gnudb', 'win.lookup-gnudb')
        section.append('MusicBrainz link…', 'win.lookup-mb-url')
        menu.append_section(None, section)
        self.lookup_button.set_menu_model(menu)

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

    def _on_rip_clicked(self, *_args) -> None:
        if self._ripping:
            if self._rip_engine is not None:
                self._rip_engine.cancel()
                self.rip_title_label.set_label('Ripping disc')
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
                    embed_art = self._artwork_embed or full
                    self._show_artwork(full, announce=True)
            except Exception:
                pass
        elif folder_art is not None and embed_art is None:
            self._prepare_embed_artwork()
            embed_art = self._artwork_embed or folder_art

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
            mp3_quality=settings.mp3_quality,
            aac_quality=settings.aac_quality,
            aac_bitrate=settings.aac_bitrate,
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
            verify_ctdb=settings.verify_ctdb,
            submit_ctdb=settings.submit_ctdb,
            sample_offset=settings.drive_sample_offset,
            disc_info=self._disc,
            disc_track_count=self._disc.track_count,
            burst_fallback=settings.burst_fallback,
            write_rip_log=settings.write_rip_log,
            write_cue_file=settings.write_cue_file,
            test_and_copy=settings.test_and_copy,
            rip_htoa=settings.rip_htoa,
            rip_data_tracks=settings.rip_data_tracks,
            copy_image=settings.copy_image,
            artwork_max_size=settings.artwork_max_size,
        )

        self._ripping = True
        self._rip_engine = RipEngine()
        self._set_ripping_ui(True)
        fmt = (job.encode_format or 'flac').upper()
        n = len(job.track_numbers)
        self.rip_title_label.set_label('Ripping disc')
        if job.copy_image:
            self.rip_status_label.set_label(f'Starting disc image ({fmt})…')
        else:
            self.rip_status_label.set_label(
                f'Starting {fmt} · {n} track{"s" if n != 1 else ""}…'
            )
        self._banner_hold = False
        self._set_progress_fraction(0.02)

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
        if (
            not self._ripping
            and not self._banner_hold
            and not self.rip_revealer.get_reveal_child()
        ):
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
        t = min(1.0, dt * 10.0)
        self._banner_shown += (self._banner_target - self._banner_shown) * t
        if abs(self._banner_target - self._banner_shown) < 0.003:
            self._banner_shown = self._banner_target
            self._apply_banner_fill()
            self._banner_tick_id = None
            return GLib.SOURCE_REMOVE
        self._apply_banner_fill()
        return GLib.SOURCE_CONTINUE

    def _apply_banner_fill(self) -> None:
        self._banner_provider.load_from_string(banner_fill_css(self._banner_shown))

    def _reset_banner_fill(self) -> bool:
        if self._ripping or self._banner_hold:
            return GLib.SOURCE_REMOVE
        self._banner_shown = 0.0
        self._banner_target = 0.0
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

    def _set_ripping_ui(self, active: bool) -> None:
        idle = not active
        if active:
            self._show_progress_panel(title='Ripping disc')
            if self._tracks_editing:
                self._tracks_editing = False
                self._apply_tracks_edit_mode()

        self.eject_button.set_sensitive(idle)
        self.empty_eject_button.set_sensitive(idle)
        self._set_action_enabled('eject', idle)
        self._set_lookup_controls_sensitive(idle and self._disc is not None)
        self.settings_button.set_sensitive(idle)
        self._set_action_enabled('preferences', idle)
        if self._track_edit_button is not None:
            self._track_edit_button.set_sensitive(idle)
        if self._track_artist_button is not None:
            self._track_artist_button.set_sensitive(idle)

        self.search_art_button.set_sensitive(idle)
        self.choose_art_button.set_sensitive(idle)
        if idle:
            self._sync_cover_action_sensitivity()
        else:
            self.clear_art_button.set_sensitive(False)
        self.album_edit_group.set_sensitive(idle)

        if active:
            self.rip_button.set_label('Cancel')
            self._set_rip_sensitive(True)
            self.rip_button.remove_css_class('suggested-action')
            self.rip_button.add_css_class('destructive-action')
        else:
            self.rip_button.set_label('Rip')
            self.rip_button.remove_css_class('destructive-action')
            self.rip_button.add_css_class('suggested-action')
            self._set_rip_sensitive(
                self._disc is not None and bool(self._disc.tracks)
            )

    def _queue_rip_progress(self, progress: RipProgress) -> None:
        """Coalesce extract progress onto the next GTK idle."""
        self._pending_progress = progress
        if self._progress_idle_id is not None:
            return
        self._progress_idle_id = GLib.idle_add(self._flush_rip_progress)

    def _flush_rip_progress(self) -> bool:
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
        elif (fallback := _RIP_STATUS_FALLBACK.get(progress.state)) is not None:
            self.rip_status_label.set_label(fallback)

    def _on_rip_finished(self, result: RipResult) -> None:
        self._ripping = False
        self._rip_engine = None
        self._set_ripping_ui(False)

        if result.cancelled:
            self.rip_title_label.set_label('Ripping disc')
            self.rip_status_label.set_label('Rip cancelled')
            self._set_progress_fraction(0.0)
            self._flash_error()
            self._hide_progress_panel(delay_ms=2500)
            self._toast('Rip cancelled')
            return

        if not result.success:
            self.rip_title_label.set_label('Ripping disc')
            self.rip_status_label.set_label(result.error or 'Rip failed')
            self._set_progress_fraction(0.0)
            self._show_progress_panel(title='Ripping disc')
            self._flash_error()
            self._hide_progress_panel(delay_ms=8000)
            self._toast(result.error or 'Rip failed')
            return

        n = len(result.output_files)
        dest_dirs = sorted(
            {str(p.parent) for p in result.output_files if p.suffix.lower() != '.cue'}
        )
        if not dest_dirs and result.album_dir is not None:
            dest_dirs = [str(result.album_dir)]
        dest = dest_dirs[0] if len(dest_dirs) == 1 else (
            f'{len(dest_dirs)} folders' if dest_dirs else 'output folder'
        )
        ar_bits = []
        if result.htoa_ripped:
            ar_bits.append('HTOA')
        if any(p.suffix.lower() == '.bin' for p in result.output_files):
            ar_bits.append('data')
        if result.cover_path is not None:
            ar_bits.append('cover saved')
        if result.accuraterip:
            matches = sum(
                1
                for r in result.accuraterip
                if r.confidence == AccurateRipConfidence.MATCH
            )
            ar_bits.append(f'AR {matches}/{len(result.accuraterip)}')
        if result.ctdb:
            matches = sum(
                1 for r in result.ctdb if r.confidence == CtdbConfidence.MATCH
            )
            ar_bits.append(f'CTDB {matches}/{len(result.ctdb)}')
        if result.burst_tracks:
            ar_bits.append(f'burst on {len(result.burst_tracks)}')
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
        self._toast(f'Rip complete · {n} file(s)', timeout=4)
        # Show per-track AR/CTDB outcome in the track list subtitles when available.
        if result.accuraterip or result.ctdb:
            self._apply_verify_results_to_rows(
                ar_results=result.accuraterip,
                ctdb_results=result.ctdb,
            )

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

        empty = _empty_tray_copy(state, device)
        if empty is not None:
            if prev is not state:
                if state is DriveTrayState.TRAY_OPEN or state is DriveTrayState.NO_DISC:
                    self._auto_rip_disc_key = None
                    self._auto_rip_pending = False
                title, description = empty
                self._clear_disc_ui_for_empty(title=title, description=description)
            return

        if state is DriveTrayState.NOT_READY:
            if prev is not DriveTrayState.NOT_READY:
                if self.stack.get_visible_child_name() == 'empty':
                    self.status_page.set_title('Drive not ready')
                    self.status_page.set_description(
                        'Waiting for the drive to finish spinning up…'
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
        self._set_rip_sensitive(False)
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

        empty = _empty_tray_copy(status.state, device)
        if empty is not None:
            title, description = empty
            self._clear_disc_ui_for_empty(title=title, description=description)
            return

        self._disc = info
        self._album = None
        self._ids = None
        self._clear_artwork()

        if info is None or not info.tracks:
            self.stack.set_visible_child_name('empty')
            self._set_rip_sensitive(False)
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
        # Switching to the disc page focuses the first EntryRow otherwise.
        self._defer_clear_window_focus()
        self._set_rip_sensitive(not self._ripping)
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

    @staticmethod
    def _format_metadata_origin(source: str | None) -> str:
        """Human label for the metadata origin banner."""
        raw = (source or '').strip().lower()
        if not raw or raw == 'manual':
            return 'No metadata'
        # Manual edits after a lookup: musicbrainz+manual, gnudb+manual, …
        base = raw.split('+', 1)[0].strip()
        names = {
            'musicbrainz': 'MusicBrainz',
            'freedb': 'gnudb',
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
        """Refresh window subtitle, album origin hint, and cover placeholder."""
        self.window_title.set_title(config.APPLICATION_NAME)
        if album is not None and (album.title or album.artist):
            artist = (album.artist or '').strip()
            title = (album.title or '').strip()
            if artist and title:
                self.window_title.set_subtitle(f'{artist} - {title}')
            else:
                self.window_title.set_subtitle(title or artist)
        elif info is not None:
            n = info.track_count
            self.window_title.set_subtitle(
                f'{n} track' if n == 1 else f'{n} tracks'
            )
        else:
            self.window_title.set_subtitle('')
        if self._artwork is None:
            self._show_placeholder_cover()

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
            self.disc_group.set_description(
                f'{info.track_count} tracks · {info.device}'
            )

        rows = [
            ('Device', info.device),
            ('Audio tracks', str(info.track_count)),
        ]
        if n_data:
            data_lab = ', '.join(
                f'#{d.number} {d.cue_mode}'
                + (f' (session {d.session})' if d.session > 1 else '')
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

    def _on_run_drive_setup(self, *_args) -> None:
        dialog = DriveSetupDialog(self.store)

        def on_closed(*_a) -> None:
            self.sync_options_from_store()
            self._update_calibration_row()
            device = self.store.get().device or self._device or '/dev/sr0'
            self._rebuild_drive_rows(device, allow_optical=False)

        dialog.connect('closed', on_closed)
        dialog.present(self)

    def _build_album_edit_rows(self) -> None:
        """Always-visible Album EntryRows (no expander)."""
        for row in self._album_field_rows:
            self.album_edit_group.remove(row)
        self._album_field_rows.clear()

        title_row = Adw.EntryRow(title='Title')
        title_row.connect('changed', self._on_album_field_changed)
        self.album_edit_group.add(title_row)
        self._album_field_rows.append(title_row)
        self._album_title_row = title_row

        artist_row = Adw.EntryRow(title='Album artist')
        artist_row.set_tooltip_text(
            'Multiple artists: separate with a semicolon '
            '(e.g. Artist One; Artist Two)'
        )
        artist_row.connect('changed', self._on_album_field_changed)
        self.album_edit_group.add(artist_row)
        self._album_field_rows.append(artist_row)
        self._album_artist_row = artist_row

        date_row = Adw.EntryRow(title='Date')
        date_row.set_tooltip_text(
            'Release date as YYYY, YYYY-MM, or YYYY-MM-DD (e.g. 1997-03-01)'
        )
        date_row.connect('changed', self._on_album_field_changed)
        self.album_edit_group.add(date_row)
        self._album_field_rows.append(date_row)
        self._album_date_row = date_row

        genre_row = Adw.EntryRow(title='Genre')
        genre_row.set_tooltip_text(
            'Album genre (e.g. rock, jazz). Top MusicBrainz genre, '
            'or gnudb / CD-Text when MB has none.'
        )
        genre_row.connect('changed', self._on_album_field_changed)
        self.album_edit_group.add(genre_row)
        self._album_field_rows.append(genre_row)
        self._album_genre_row = genre_row

        label_row = Adw.EntryRow(title='Label')
        label_row.connect('changed', self._on_album_field_changed)
        self.album_edit_group.add(label_row)
        self._album_field_rows.append(label_row)
        self._album_label_row = label_row

        disc_row = Adw.EntryRow(title='Disc')
        disc_row.set_text('1/1')
        disc_row.set_tooltip_text(
            'Disc as N/M (e.g. 1/1 or 2/3) — DISCNUMBER / TPOS'
        )
        disc_row.connect('changed', self._on_album_field_changed)
        self.album_edit_group.add(disc_row)
        self._album_field_rows.append(disc_row)
        self._album_disc_row = disc_row
        # Building rows can steal focus into Title; drop it until the user clicks.
        self._defer_clear_window_focus()

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
        self._suppress_meta_write = True
        try:
            if self._album_title_row is not None:
                self._album_title_row.set_text(album.title if album else '')
            if self._album_artist_row is not None:
                self._album_artist_row.set_text(
                    normalize_artists(album.artist) if album else ''
                )
            if self._album_date_row is not None:
                self._album_date_row.set_text(
                    normalize_release_date(album.date) if album else ''
                )
            if self._album_genre_row is not None:
                self._album_genre_row.set_text(album.genre if album else '')
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
        album = self._ensure_album_tracks()
        if self._album_title_row is not None:
            album.title = self._album_title_row.get_text().strip()
        if self._album_artist_row is not None:
            album.artist = normalize_artists(self._album_artist_row.get_text())
        if self._album_date_row is not None:
            album.date = normalize_release_date(self._album_date_row.get_text())
        if self._album_genre_row is not None:
            album.genre = self._album_genre_row.get_text().strip()
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
        group = self.track_edit_group
        for row in self._track_rows:
            group.remove(row)
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
        self._track_artist_button = None
        self._track_select_all = None
        if self._track_header_suffix is not None:
            group.set_header_suffix(None)
            self._track_header_suffix = None
        # New disc/list always starts in view mode.
        self._tracks_editing = False
        # Fresh size groups so rebuilt widgets align cleanly.
        self._track_title_size = Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL)
        self._track_artist_size = Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL)

        if info is None:
            group.set_title('Tracks')
            return

        # Edit flow: Edit/Done always; “Album artist” only while editing.
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        btn_edit = Gtk.Button(label='Edit')
        btn_edit.add_css_class('flat')
        btn_edit.set_tooltip_text('Edit titles and artists')
        btn_edit.connect('clicked', self._on_tracks_edit_toggled)
        btn_artist = Gtk.Button(label='Album artist')
        btn_artist.add_css_class('flat')
        btn_artist.set_tooltip_text(
            'Copy the album artist into tracks that have no artist'
        )
        btn_artist.set_visible(False)
        btn_artist.connect('clicked', lambda *_: self._fill_empty_track_artists())
        header_box.append(btn_artist)
        header_box.append(btn_edit)
        group.set_header_suffix(header_box)
        self._track_header_suffix = header_box
        self._track_edit_button = btn_edit
        self._track_artist_button = btn_artist

        # Column header: Title | Artist — keeps artist column aligned under “Tracks”.
        col_header = self._make_track_column_header()
        group.add(col_header)
        self._track_rows.append(col_header)

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
                group.add(row)
                self._track_rows.append(row)
        finally:
            self._suppress_meta_write = False

        group.set_title(f'Tracks · {info.track_count}')
        self._apply_tracks_edit_mode()

    @staticmethod
    def _make_title_artist_grid(title: Gtk.Widget, artist: Gtk.Widget) -> Gtk.Grid:
        """Homogeneous 2-col grid: Title and Artist each take half."""
        grid = Gtk.Grid()
        grid.set_column_homogeneous(True)
        grid.set_column_spacing(12)
        grid.set_hexpand(True)
        grid.set_halign(Gtk.Align.FILL)
        for child in (title, artist):
            child.set_hexpand(True)
            child.set_halign(Gtk.Align.FILL)
        grid.attach(title, 0, 0, 1, 1)
        grid.attach(artist, 1, 0, 1, 1)
        return grid

    @staticmethod
    def _track_caption(
        text: str,
        *extra_classes: str,
        xalign: float = 0.0,
    ) -> Gtk.Label:
        label = Gtk.Label(label=text)
        label.add_css_class('caption')
        label.add_css_class('dim-label')
        for name in extra_classes:
            label.add_css_class(name)
        label.set_xalign(xalign)
        return label

    def _make_track_field_stack(
        self,
        *,
        number: int,
        value: str,
        empty_display: str,
        placeholder: str,
        tooltip: str,
        css_class: str | None = None,
    ) -> tuple[Gtk.Stack, Gtk.Label, Gtk.Entry]:
        stack = Gtk.Stack()
        stack.set_transition_type(Gtk.StackTransitionType.NONE)
        if css_class:
            stack.add_css_class(css_class)

        display = value or empty_display
        label = Gtk.Label(label=display)
        label.set_hexpand(True)
        label.set_halign(Gtk.Align.START)
        label.set_xalign(0.0)
        label.set_ellipsize(Pango.EllipsizeMode.END)
        label.set_valign(Gtk.Align.CENTER)
        if not value:
            label.add_css_class('dim-label')
        stack.add_named(label, 'view')

        entry = Gtk.Entry()
        entry.set_text(value)
        entry.set_placeholder_text(placeholder)
        entry.set_hexpand(True)
        entry.add_css_class('ready2rip-track-entry')
        entry.set_tooltip_text(tooltip)
        entry.connect(
            'changed',
            lambda *_a, n=number: self._on_track_field_changed(n),
        )
        stack.add_named(entry, 'edit')
        stack.set_visible_child_name('view')
        return stack, label, entry

    def _make_track_column_header(self) -> Gtk.ListBoxRow:
        """Column titles so Title / Artist line up with track cells."""
        row = Gtk.ListBoxRow()
        row.set_activatable(False)
        row.set_selectable(False)
        row.add_css_class('ready2rip-track-header')

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        box.add_css_class('ready2rip-track-row')
        box.set_hexpand(True)

        lead = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        lead.add_css_class('ready2rip-track-lead')
        select_all = Gtk.CheckButton(active=True)
        select_all.set_valign(Gtk.Align.CENTER)
        select_all.set_tooltip_text('Select or deselect all tracks for ripping')
        select_all.connect('toggled', self._on_select_all_toggled)
        self._track_select_all = select_all
        num_ph = self._track_caption('#', 'ready2rip-track-num', xalign=0.5)
        lead.append(select_all)
        lead.append(num_ph)
        box.append(lead)

        title_h = self._track_caption('Title', 'heading')
        title_h.set_halign(Gtk.Align.START)
        self._track_title_size.add_widget(title_h)
        artist_h = self._track_caption('Artist', 'heading', 'ready2rip-track-artist')
        artist_h.set_halign(Gtk.Align.START)
        self._track_artist_size.add_widget(artist_h)
        box.append(self._make_title_artist_grid(title_h, artist_h))

        dur_h = self._track_caption('Time', 'ready2rip-track-dur', xalign=1.0)
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
        Title/Artist share remaining width equally (1/2 each).
        """
        row = Gtk.ListBoxRow()
        row.set_activatable(False)
        row.set_selectable(False)
        row.set_tooltip_text(f'Track {number:02d} · {duration_label}')

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        box.add_css_class('ready2rip-track-row')
        box.set_hexpand(True)

        lead = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        lead.add_css_class('ready2rip-track-lead')
        check = Gtk.CheckButton(active=True)
        check.set_valign(Gtk.Align.CENTER)
        check.set_tooltip_text(f'Include track {number:02d} in the rip')
        check.connect('toggled', self._on_track_check_toggled)
        num = Gtk.Label(label=f'{number:02d}')
        num.add_css_class('ready2rip-track-num')
        num.add_css_class('dim-label')
        num.set_valign(Gtk.Align.CENTER)
        num.set_xalign(0.5)
        lead.append(check)
        lead.append(num)
        box.append(lead)

        title_stack, title_label, title_entry = self._make_track_field_stack(
            number=number,
            value=title,
            empty_display=f'Track {number:02d}',
            placeholder=f'Track {number:02d} title',
            tooltip=f'Title for track {number:02d}',
        )
        self._track_title_size.add_widget(title_stack)
        artist_stack, artist_label, artist_entry = self._make_track_field_stack(
            number=number,
            value=artist,
            empty_display='—',
            placeholder='Artist One; Artist Two',
            tooltip=(
                f'Artist for track {number:02d}. '
                'Multiple artists: separate with a semicolon (e.g. A; B)'
            ),
            css_class='ready2rip-track-artist',
        )
        self._track_artist_size.add_widget(artist_stack)
        box.append(self._make_title_artist_grid(title_stack, artist_stack))

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

        dur = self._track_caption(duration_label, 'ready2rip-track-dur', xalign=1.0)
        dur.set_valign(Gtk.Align.CENTER)
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
        if self._track_artist_button is not None:
            self._track_artist_button.set_visible(editing)

        child = 'edit' if editing else 'view'
        for num in self._track_title_entries:
            ts = self._track_title_stacks.get(num)
            as_ = self._track_artist_stacks.get(num)
            if ts is not None:
                ts.set_visible_child_name(child)
            if as_ is not None:
                as_.set_visible_child_name(child)

        if editing and self._track_title_entries:
            first = min(self._track_title_entries)
            self._track_title_entries[first].grab_focus()

    def _sync_track_labels_from_entries(self) -> None:
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
        self._syncing_select_all = True
        try:
            for check in self._track_checks.values():
                check.set_active(active)
            if self._track_select_all is not None:
                self._track_select_all.set_active(active)
                self._track_select_all.set_inconsistent(False)
        finally:
            self._syncing_select_all = False

    def _on_select_all_toggled(self, button: Gtk.CheckButton) -> None:
        if self._syncing_select_all:
            return
        self._set_all_tracks(button.get_active())

    def _on_track_check_toggled(self, *_args) -> None:
        if self._syncing_select_all or self._track_select_all is None:
            return
        checks = list(self._track_checks.values())
        if not checks:
            return
        active = sum(1 for c in checks if c.get_active())
        self._syncing_select_all = True
        try:
            if active == 0:
                self._track_select_all.set_active(False)
                self._track_select_all.set_inconsistent(False)
            elif active == len(checks):
                self._track_select_all.set_active(True)
                self._track_select_all.set_inconsistent(False)
            else:
                self._track_select_all.set_inconsistent(True)
        finally:
            self._syncing_select_all = False

    def _fill_empty_track_artists(self) -> None:
        """Copy album artist (incl. multi ``A; B``) into empty track artists."""
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
                f'Applied album artist to {filled} track{"s" if filled != 1 else ""}'
            )
        else:
            self._toast('All tracks already have an artist')

    def _apply_verify_results_to_rows(
        self,
        *,
        ar_results=None,
        ctdb_results=None,
    ) -> None:
        ar_by = {r.track_number: r for r in (ar_results or [])}
        ctdb_by = {r.track_number: r for r in (ctdb_results or [])}
        for num in sorted(set(ar_by) | set(ctdb_by)):
            label = self._track_status_labels.get(num)
            if label is None:
                continue
            parts: list[str] = []
            ar = ar_by.get(num)
            if ar is not None and ar.message:
                parts.append(ar.message)
            ctdb = ctdb_by.get(num)
            if ctdb is not None and ctdb.message:
                parts.append(ctdb.message)
            msg = ' · '.join(parts)
            label.set_text(msg)
            label.set_tooltip_text(msg)
            label.set_visible(bool(msg))

    # —— Metadata lookup ——

    def _start_metadata_lookup(
        self,
        *,
        interactive: bool,
        use_musicbrainz: bool | None = None,
        use_freedb: bool | None = None,
    ) -> None:
        if self._disc is None or self._ids is None or self._looking_up:
            return

        settings = self.store.get()
        use_mb = (
            settings.use_musicbrainz
            if use_musicbrainz is None
            else use_musicbrainz
        )
        use_fb = settings.use_freedb if use_freedb is None else use_freedb
        if not use_mb and not use_fb:
            if interactive:
                self._toast('Enable MusicBrainz or gnudb under Metadata options')
            return

        self._lookup_generation += 1
        generation = self._lookup_generation
        self._looking_up = True
        self._set_lookup_controls_sensitive(False)
        self.lookup_progress.set_visible(True)
        self.lookup_progress.pulse()

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
            self._toast(f'Lookup failed: {error}', timeout=6)
            return

        if not results:
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
        origin = self._format_metadata_origin(album.source)
        if origin != 'No metadata':
            self._toast(origin)
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
            self._toast('Enable Download artwork and at least one source.')
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
            return
        max_edge = self.store.get().artwork_max_size
        self._artwork_embed = ArtworkFetcher().resize(self._artwork, max_edge)

    def _show_artwork(self, image: ArtworkImage, *, announce: bool = False) -> None:
        """Show *image* full-bleed in the cover frame."""
        size = getattr(self, '_cover_display_size', self._COVER_SIZE)
        self.cover_picture.set_content_fit(Gtk.ContentFit.COVER)
        self._apply_cover_display_size(size)

        if not apply_artwork_to_picture(
            self.cover_picture, image, edge=size
        ):
            self._show_placeholder_cover()
            self._sync_cover_action_sensitivity()
            return

        self.cover_placeholder.set_visible(False)
        self._set_cover_frame_empty(False)
        self._update_cover_size_label()
        self._sync_cover_action_sensitivity()
        if announce:
            self._toast(self._format_art_origin(image.source))

    def _toast(self, title: str, timeout: int = 3) -> None:
        """Show a transient bottom toast (Adwaita / GNOME HIG).

        Auto-dismisses after *timeout* seconds. Timeout is clamped so toasts
        never stick forever (``0`` in libadwaita means no auto-dismiss).
        """
        # GNOME short notifications are typically ~2–5 s.
        seconds = max(2, min(int(timeout), 5))
        # Adw.Toast titles are Pango markup — escape &, <, etc. from metadata.
        toast = Adw.Toast(title=GLib.markup_escape_text(title))
        toast.set_timeout(seconds)
        try:
            toast.set_priority(Adw.ToastPriority.NORMAL)
        except (AttributeError, TypeError):
            pass
        self.toast_overlay.add_toast(toast)


def _mark_album_edited(album: AlbumMetadata) -> None:
    """Annotate metadata source after a manual edit."""
    source = album.source or ''
    if source in ('musicbrainz', 'gnudb', 'freedb') and not source.endswith(
        '+manual'
    ):
        album.source = f'{source}+manual'
    elif not source:
        album.source = 'manual'
