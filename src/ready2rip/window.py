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
from ready2rip.artwork.fetch import (
    ArtworkImage,
    apply_artwork_to_picture,
    blank_cover_texture,
)
from ready2rip.disc.discid_util import DiscIdentifiers
from ready2rip.disc.drive_info import DriveInfo
from ready2rip.disc.drive_status import (
    DriveStatus,
    DriveTrayState,
)
from ready2rip.disc.probe import DiscInfo
from ready2rip.icons import apply_icon
from ready2rip.metadata.album_edit import mark_album_edited
from ready2rip.metadata.cache import MetadataCache
from ready2rip.metadata.models import AlbumMetadata, TrackMetadata
from ready2rip.paths import (
    DEFAULT_ALBUM_FOLDER_TEMPLATE,
    DEFAULT_FILENAME_TEMPLATE,
    FILENAME_SCHEMES,
    FILENAME_TEMPLATE_HELP,
    FOLDER_SCHEMES,
    FOLDER_TEMPLATE_HELP,
    NamingScheme,
    scheme_index,
    scheme_tooltip,
    track_meta_for,
)
from ready2rip.rip.encode import qaac_available
from ready2rip.rip.engine import RipEngine, RipProgress
from ready2rip.settings import (
    AAC_QUALITIES,
    ALSO_ENCODERS,
    ALSO_FOLDER_LAYOUTS,
    ARTWORK_SIZES,
    DEFAULT_ARTWORK_MAX_SIZE,
    ENCODERS,
    FLAC_LEVELS,
    MP3_QUALITIES,
    OPUS_BITRATES,
    QAAC_QUALITIES,
    WAVPACK_LEVELS,
    SettingsStore,
    default_output_directory,
    normalize_aac_quality,
    normalize_also_encode_folder,
    normalize_also_encode_format,
    normalize_mp3_quality,
    normalize_qaac_quality,
    normalize_wavpack_level,
    opus_bitrate_label,
)
from ready2rip.tags.artists import ARTIST_STYLES, normalize_artists
from ready2rip.ui.window_drive import DriveMonitorMixin
from ready2rip.ui.window_metadata import MetadataActionsMixin
from ready2rip.ui.window_rip import RipActionsMixin
from ready2rip.util import normalize_release_date, parse_disc_field, validate_device_path
from ready2rip.window_styles import install_window_styles

log = logging.getLogger(__name__)

_WINDOW_UI = str(Path(__file__).with_name('window.ui'))

# Settings header switcher → bottom bar below this dialog width.
_SETTINGS_NARROW_BREAKPOINT = 'max-width: 550sp'

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


@Gtk.Template(filename=_WINDOW_UI)
class Ready2RipWindow(
    RipActionsMixin,
    DriveMonitorMixin,
    MetadataActionsMixin,
    Adw.ApplicationWindow,
):
    __gtype_name__ = 'Ready2RipWindow'

    toast_overlay = Gtk.Template.Child()
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
    art_album_row = Gtk.Template.Child()
    art_group = Gtk.Template.Child()
    art_cover_row = Gtk.Template.Child()
    cover_frame = Gtk.Template.Child()
    cover_overlay = Gtk.Template.Child()
    cover_picture = Gtk.Template.Child()
    cover_placeholder = Gtk.Template.Child()
    cover_hover = Gtk.Template.Child()
    cover_actions = Gtk.Template.Child()
    cover_size_label = Gtk.Template.Child()
    cover_embed_lossy_label = Gtk.Template.Child()
    cover_embed_lossless_label = Gtk.Template.Child()
    search_art_button = Gtk.Template.Child()
    choose_art_button = Gtk.Template.Child()
    clear_art_button = Gtk.Template.Child()
    rip_revealer = Gtk.Template.Child()
    rip_banner = Gtk.Template.Child()
    rip_title_label = Gtk.Template.Child()
    rip_status_label = Gtk.Template.Child()

    _COVER_SIZE = 280
    _COVER_SIZE_MIN = 200
    _COVER_SIZE_MAX = 560
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
        self._track_title_fields: dict[int, Gtk.Widget] = {}
        self._track_artist_fields: dict[int, Gtk.Widget] = {}
        self._tracks_editing = False
        self._track_edit_button: Gtk.Button | None = None
        self._track_artist_button: Gtk.Button | None = None
        self._track_select_all: Gtk.CheckButton | None = None
        self._syncing_select_all = False
        self._naming_scheme_factories: list[Gtk.SignalListItemFactory] = []
        # Last Custom templates (kept when switching to presets; restored on Custom).
        self._custom_album_folder_template = ''
        self._custom_filename_template = ''
        # Last lossy-encode custom root (cleared from the entry when leaving Custom).
        self._custom_also_directory = ''
        # Lead (#) and Time stay locked; Title|Artist use a fixed 50/50 grid
        # so column headers do not drift when the window is resized.
        self._track_lead_size = Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL)
        self._track_dur_size = Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL)
        self._album_field_rows: list[Gtk.Widget] = []
        self._album_title_row: Adw.EntryRow | None = None
        self._album_artist_row: Adw.EntryRow | None = None
        self._album_date_row: Adw.EntryRow | None = None
        self._album_genre_row: Adw.EntryRow | None = None
        self._album_disc_row: Adw.EntryRow | None = None
        self._track_header_suffix: Gtk.Widget | None = None
        self._disc_rows: list[Adw.ActionRow] = []
        self._drive_rows: list[Gtk.Widget] = []
        self._lookup_generation = 0
        self._looking_up = False
        self._lookup_toast: Adw.Toast | None = None
        self._setup_dialog_open = False
        self._pending_rip_after_setup = False
        self._pending_auto_lookup = False
        self._artwork: ArtworkImage | None = None
        self._artwork_embed: ArtworkImage | None = None  # lossy embed size
        self._artwork_embed_lossless: ArtworkImage | None = None
        self._art_generation = 0
        self._lyrics_generation = 0
        self._ripping = False
        self._rip_engine: RipEngine | None = None
        self._rip_started_at = 0.0
        self._cancel_rip_dialog: Adw.AlertDialog | None = None
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
        self._progress_queue_lock = threading.Lock()
        self._banner_shown = 0.0
        self._banner_target = 0.0
        self._banner_css_frac = -1.0
        self._banner_tick_id: int | None = None
        self._banner_last_us = 0
        self._banner_hold = False
        self._banner_provider = Gtk.CssProvider()
        self._flash_id: int | None = None
        self._cover_fit_id: int | None = None
        self._drive_tray_row: Adw.ActionRow | None = None
        self.store = store if store is not None else SettingsStore()
        self._device = self.store.get().device

        self._settings_dialog: Adw.Dialog | None = None
        # Preferences pages: Ripping / Encoding / Metadata / Drive
        self.verify_group = Adw.PreferencesGroup(
            title='Verify',
            description=(
                'Secure = local CRC; AccurateRip/CTDB = online confirm'
            ),
        )
        self.extras_group = Adw.PreferencesGroup(
            title='Extras',
            description='Disc image, hidden audio, logs, and CUE sheets',
        )
        self.automation_group = Adw.PreferencesGroup(title='Automation')
        self.encoding_group = Adw.PreferencesGroup(
            title='Encoding',
            description='Primary lossless format and optional lossy companion',
        )
        self.files_group = Adw.PreferencesGroup(
            title='Files',
            description='Where ripped albums are written',
        )
        self.naming_group = Adw.PreferencesGroup(
            title='Naming',
            description='Album folders, track filenames, and multi-artist style',
        )
        self.device_group = Adw.PreferencesGroup(title='Device')
        self.lookup_group = Adw.PreferencesGroup(
            title='Lookup',
            description='Online metadata sources',
        )
        self.artwork_group = Adw.PreferencesGroup(
            title='Artwork',
            description='Download covers and embed them into audio files',
        )
        self.tags_group = Adw.PreferencesGroup(
            title='Tags',
            description=(
                'Loudness, lyrics, and Encoded by (ready2rip). '
                'Encoder always names the codec'
            ),
        )
        self.disc_group = Adw.PreferencesGroup(title='Disc')
        self.drive_group = Adw.PreferencesGroup(title='Drive')
        self._flac_levels = [level for level, _label in FLAC_LEVELS]
        self._wavpack_levels = [level for level, _label in WAVPACK_LEVELS]
        self._mp3_qualities = [qid for qid, _label in MP3_QUALITIES]
        self._aac_qualities = [qid for qid, _label in AAC_QUALITIES]
        self._qaac_qualities = [qid for qid, _label in QAAC_QUALITIES]
        self._opus_values = list(OPUS_BITRATES)
        self._also_formats = [fid for fid, _label in ALSO_ENCODERS]
        self._also_folders = [fid for fid, _label in ALSO_FOLDER_LAYOUTS]
        self._quality_kind: str | None = None
        self._also_quality_kind: str | None = None

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
        self.status_page.set_description(f'Looking for an audio CD on {device}…')
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
        apply_icon(self.settings_button, 'preferences-system-symbolic', display=display)
        apply_icon(self.lookup_button, 'system-search-symbolic', display=display)
        apply_icon(self.eject_button, 'media-eject-symbolic', display=display)
        apply_icon(self.search_art_button, 'system-search-symbolic', display=display)
        apply_icon(self.choose_art_button, 'folder-symbolic', display=display)
        apply_icon(self.clear_art_button, 'user-trash-symbolic', display=display)
        # Empty-state status page is text-only (no disc icon), same as tray-open.
        self.status_page.set_icon_name(None)
        apply_icon(self.cover_placeholder, 'folder-music-symbolic', display=display)

    def _on_close_request(self, *_args) -> bool:
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
        """Settings dialog with header switcher (wide) or bottom bar (narrow)."""
        pages: list[tuple[str, str, tuple[Adw.PreferencesGroup, ...]]] = [
            (
                'Ripping',
                'media-optical-symbolic',
                (self.verify_group, self.extras_group, self.automation_group),
            ),
            (
                'Encoding',
                'document-properties-symbolic',
                (self.encoding_group, self.files_group, self.naming_group),
            ),
            (
                'Metadata',
                'audio-x-generic-symbolic',
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
        # Crossfade between settings pages (Adw.ViewStack, libadwaita ≥ 1.7).
        # AppImage GNOME prefix may be older; skip when unavailable.
        if hasattr(stack, 'set_enable_transitions'):
            stack.set_enable_transitions(True)
            stack.set_transition_duration(200)

        switcher = Adw.ViewSwitcher(stack=stack)
        switcher.set_policy(Adw.ViewSwitcherPolicy.WIDE)

        # Wide: page switcher in the header. Narrow: “Settings” title + bottom bar.
        title_stack = Gtk.Stack()
        title_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        title_stack.set_transition_duration(150)
        title_stack.add_named(switcher, 'switcher')
        title_label = Gtk.Label(label='Settings')
        title_label.add_css_class('title')
        title_stack.add_named(title_label, 'title')
        title_stack.set_visible_child_name('switcher')
        header.set_title_widget(title_stack)
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
            stack.add_titled_with_icon(scrolled, title.lower(), title, icon)

        toolbar.set_content(stack)

        switcher_bar = Adw.ViewSwitcherBar(stack=stack)
        switcher_bar.set_reveal(False)
        toolbar.add_bottom_bar(switcher_bar)

        # GNOME Settings–style: bottom switcher when the dialog is narrow.
        if (
            hasattr(dialog, 'add_breakpoint')
            and hasattr(Adw, 'Breakpoint')
            and hasattr(Adw, 'BreakpointCondition')
        ):
            try:
                condition = Adw.BreakpointCondition.parse(_SETTINGS_NARROW_BREAKPOINT)
                breakpoint = Adw.Breakpoint.new(condition)
                breakpoint.add_setter(title_stack, 'visible-child-name', 'title')
                breakpoint.add_setter(switcher_bar, 'reveal', True)
                dialog.add_breakpoint(breakpoint)
            except Exception:
                log.debug('Settings narrow breakpoint unavailable', exc_info=True)

        dialog.set_child(toolbar)
        self._settings_dialog = dialog

    def _on_preferences(self, *_args) -> None:
        if self._settings_dialog is None:
            self._setup_settings_dialog()
        assert self._settings_dialog is not None
        self.sync_options_from_store()
        self._settings_dialog.present(self)

    def _setup_cover_widget(self) -> None:
        """Square cover beside Album; actions on hover."""
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
        self.art_album_row.connect('notify::width', self._on_art_album_geometry)
        self.album_edit_group.connect('notify::height', self._on_art_album_geometry)

    def _attach_art_album_grid(self) -> None:
        """Place Artwork beside Album; cover square matches Album list height."""
        grid = self.art_album_row
        art = self.art_group
        album = self.album_edit_group
        if art.get_parent() is grid:
            grid.remove(art)
        if album.get_parent() is grid:
            grid.remove(album)
        # Non-homogeneous: art column tracks the square cover; Album fills rest.
        grid.set_column_homogeneous(False)
        grid.set_column_spacing(18)
        art.set_hexpand(False)
        art.set_halign(Gtk.Align.START)
        album.set_hexpand(True)
        album.set_halign(Gtk.Align.FILL)
        grid.attach(art, 0, 0, 1, 1)
        grid.attach(album, 1, 0, 1, 1)

    def _on_art_album_geometry(self, *_args) -> None:
        if self._cover_fit_id is not None:
            return
        self._cover_fit_id = GLib.timeout_add(32, self._run_fit_cover)

    def _run_fit_cover(self) -> bool:
        self._cover_fit_id = None
        self._fit_cover_to_column()
        return GLib.SOURCE_REMOVE

    @staticmethod
    def _preferences_listbox(group: Adw.PreferencesGroup) -> Gtk.ListBox | None:
        """Return the boxed-list inside an AdwPreferencesGroup."""
        outer = group.get_first_child()
        if outer is None:
            return None
        header = outer.get_first_child()
        content = header.get_next_sibling() if header is not None else None
        if content is None:
            return None
        listbox = content.get_first_child()
        return listbox if isinstance(listbox, Gtk.ListBox) else None

    def _album_fields_height(self) -> int:
        """Pixel height of the Album boxed-list (Title through Disc)."""
        listbox = self._preferences_listbox(self.album_edit_group)
        if listbox is not None:
            h = listbox.get_height()
            if h > 0:
                return h
        # Fallback before allocate: sum mapped rows.
        total = 0
        measured = 0
        for row in self._album_field_rows:
            h = row.get_height()
            if h > 0:
                total += h
                measured += 1
        if measured == 0:
            return 0
        n = len(self._album_field_rows)
        if n > measured:
            total = round(total * (n / measured))
        return total

    def _cover_size_for_row(self) -> int | None:
        """Square edge = exact Album list height (Title→Disc)."""
        fields_h = self._album_fields_height()
        if fields_h <= 0:
            return None
        # Match the list height exactly so cover and Album block stay even.
        return max(self._COVER_SIZE_MIN, min(self._COVER_SIZE_MAX, fields_h))

    def _fit_cover_to_column(self) -> None:
        """Keep the cover square equal to the Album Title→Disc list height."""
        size = self._cover_size_for_row()
        if size is None:
            return
        if size == getattr(self, '_cover_display_size', None):
            return
        self._apply_cover_display_size(size)
        if self._artwork is not None:
            apply_artwork_to_picture(self.cover_picture, self._artwork, edge=size)
        else:
            self._apply_blank_cover_paintable(size)

    def _apply_cover_display_size(self, size: int) -> None:
        self._cover_display_size = size
        for widget in (
            self.cover_overlay,
            self.cover_picture,
            self.cover_frame,
            self.art_cover_row,
        ):
            widget.set_size_request(size, size)
        # Keep the Artwork column as wide as the square (title chrome matches Album).
        self.art_group.set_size_request(size, -1)
        icon_px = max(32, min(self._COVER_PLACEHOLDER_ICON_SIZE, size // 4))
        self.cover_placeholder.set_pixel_size(icon_px)

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
        install_window_styles(self.get_display())
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
        opacity = 1.0 if visible else 0.0
        self.cover_hover.set_opacity(opacity)
        self.cover_actions.set_opacity(opacity)
        for button in (
            self.search_art_button,
            self.choose_art_button,
            self.clear_art_button,
        ):
            button.set_opacity(opacity)
        if visible:
            self._update_cover_size_label()

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

        apply_icon(self.cover_placeholder, 'folder-music-symbolic', display=self.get_display())
        self.cover_placeholder.add_css_class('ready2rip-cover-placeholder')
        self.cover_placeholder.set_visible(True)
        self.cover_placeholder.set_opacity(1.0)
        self._set_cover_frame_empty(True)
        self._update_cover_size_label()
        self._fit_cover_to_column()

    def _update_cover_size_label(self) -> None:
        art = self._artwork
        if art is None:
            self.cover_size_label.set_visible(False)
            self.cover_size_label.set_text('')
            self.cover_embed_lossy_label.set_visible(False)
            self.cover_embed_lossy_label.set_text('')
            self.cover_embed_lossless_label.set_visible(False)
            self.cover_embed_lossless_label.set_text('')
            return

        self.cover_size_label.set_text(f'{art.width}×{art.height}')
        self.cover_size_label.set_tooltip_text('Source artwork size')
        self.cover_size_label.set_visible(True)

        settings = self.store.get()
        if settings.wants_embed_artwork() and (
            self._artwork_embed is None and self._artwork_embed_lossless is None
        ):
            self._prepare_embed_artwork()

        if settings.embed_artwork and self._artwork_embed is not None:
            emb = self._artwork_embed
            self.cover_embed_lossy_label.set_text(f'Lossy {emb.width}×{emb.height}')
            self.cover_embed_lossy_label.set_tooltip_text(
                f'Lossy embed size ({emb.width}×{emb.height})'
            )
            self.cover_embed_lossy_label.set_visible(True)
        else:
            self.cover_embed_lossy_label.set_text('')
            self.cover_embed_lossy_label.set_visible(False)

        if settings.embed_artwork_lossless and self._artwork_embed_lossless is not None:
            emb_l = self._artwork_embed_lossless
            self.cover_embed_lossless_label.set_text(
                f'Lossless {emb_l.width}×{emb_l.height}'
            )
            self.cover_embed_lossless_label.set_tooltip_text(
                f'Lossless embed size ({emb_l.width}×{emb_l.height})'
            )
            self.cover_embed_lossless_label.set_visible(True)
        else:
            self.cover_embed_lossless_label.set_text('')
            self.cover_embed_lossless_label.set_visible(False)

    # —— Settings groups (Preferences dialog) ——

    def _build_calibration_row(self) -> None:
        """Detect row on the Drive page; Sample offset nests inside the expander."""
        settings = self.store.get()
        self._setup_row = Adw.ExpanderRow(title='Detect sample offset')
        self._setup_row.set_tooltip_text(
            "Measure this drive's AccurateRip sample offset"
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
        self._setup_btn.connect('clicked', self.present_drive_setup)
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

        self._add_pref(self.device_group, self._setup_row)
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
            row.set_tooltip_text("Measure this drive's AccurateRip sample offset")
            self._setup_ok_icon.set_visible(False)
            btn.set_icon_name('')
            btn.set_label('Detect')
            btn.add_css_class('suggested-action')
            btn.set_tooltip_text('Detect sample offset for this drive')
            self._offset_row.set_tooltip_text('Not calibrated yet')

    @staticmethod
    def _add_pref(group: Adw.PreferencesGroup, row: Gtk.Widget) -> None:
        group.add(row)

    @staticmethod
    def _set_feature_expander(row: Adw.ExpanderRow, enabled: bool) -> None:
        """Keep enable-switch expanders in lockstep (no orphan open bodies)."""
        enabled = bool(enabled)
        row.set_enable_expansion(enabled)
        row.set_expanded(enabled)

    @staticmethod
    def _select_combo(row: Adw.ComboRow, values: list[str], value: str) -> None:
        try:
            row.set_selected(values.index(value))
        except ValueError:
            row.set_selected(0)

    def _folder_browse_button(self, on_clicked) -> Gtk.Button:
        btn = Gtk.Button()
        apply_icon(btn, 'folder-open-symbolic', display=self.get_display())
        btn.add_css_class('flat')
        btn.set_valign(Gtk.Align.CENTER)
        btn.set_tooltip_text('Choose folder')
        btn.connect('clicked', on_clicked)
        return btn

    def _make_switch_row(
        self,
        *,
        title: str,
        tooltip: str,
        active: bool,
        handler,
    ) -> Adw.SwitchRow:
        row = Adw.SwitchRow(title=title, active=active)
        row.set_tooltip_text(tooltip)
        row.connect('notify::active', handler)
        return row

    def _make_feature_expander(
        self,
        *,
        title: str,
        tooltip: str,
        enabled: bool,
        handler,
    ) -> Adw.ExpanderRow:
        row = Adw.ExpanderRow(title=title)
        row.set_tooltip_text(tooltip)
        row.set_show_enable_switch(True)
        self._set_feature_expander(row, enabled)
        row.connect('notify::enable-expansion', handler)
        return row

    def _apply_feature_enable(
        self,
        row: Adw.ExpanderRow,
        store_key: str,
        *,
        after: Callable[[bool], None] | None = None,
    ) -> bool:
        """Persist an enable-switch expander and keep expand state matched."""
        enabled = bool(row.get_enable_expansion())
        self.store.update(**{store_key: enabled})
        if row.get_expanded() != enabled:
            row.set_expanded(enabled)
        if after is not None:
            after(enabled)
        return enabled

    def _build_options_rows(self) -> None:
        """Build Ripping / Encoding / Drive preference rows."""
        settings = self.store.get()
        default_out = default_output_directory()
        ids = [e[0] for e in ENCODERS]

        self._build_drive_settings(settings)
        self._build_encoding_settings(settings, ids)
        self._build_files_naming_settings(settings, default_out)
        self._build_ripping_settings(settings)

    def _build_drive_settings(self, settings) -> None:
        from ready2rip.ui.prefs_drive import build_drive_settings

        build_drive_settings(self, settings)

    def _set_overread_status(self, label: str, *, detail: str = '') -> None:
        """Update Drive → Overread row (main thread). Prefer ``_update_overread_row``."""
        row = getattr(self, '_overread_row', None)
        if row is None:
            return
        text = (label or 'Unknown').strip() or 'Unknown'
        if detail:
            row.set_subtitle(f'{text} · {detail}')
        else:
            row.set_subtitle(text)

    def _update_overread_row(self) -> None:
        """Refresh Drive → Overread from stored probe + current sample offset."""
        from ready2rip.disc.overread import (
            capability_from_settings,
            format_overread_settings_status,
        )

        row = getattr(self, '_overread_row', None)
        if row is None:
            return
        # Rip worker may persist via a different SettingsStore instance.
        self.store.invalidate()
        settings = self.store.get()
        overread = capability_from_settings(
            lead_in=settings.drive_overread_lead_in,
            lead_out=settings.drive_overread_lead_out,
        )
        probed = bool(settings.drive_overread_probed)
        # Treat a probe for a different device path as stale.
        device = (settings.device or '').strip()
        saved_dev = (settings.drive_overread_device or '').strip()
        if probed and device and saved_dev:
            try:
                import os

                if os.path.realpath(device) != os.path.realpath(saved_dev):
                    probed = False
            except OSError:
                if device != saved_dev:
                    probed = False
        subtitle, tip = format_overread_settings_status(
            probed=probed,
            overread=overread,
            sample_offset=int(settings.drive_sample_offset),
        )
        row.set_subtitle(subtitle)
        row.set_tooltip_text(tip)

    def _schedule_overread_probe(self) -> None:
        """Background MMC overread probe after offset save (disc preferred)."""
        device = self.store.get().device or self._device or '/dev/sr0'
        store = self.store

        def work() -> None:
            try:
                from ready2rip.drive_setup import probe_and_persist_overread

                probe_and_persist_overread(store, device=device)
            except Exception:
                pass

            def done() -> bool:
                self._update_overread_row()
                return False

            GLib.idle_add(done)

        threading.Thread(target=work, name='ready2rip-overread', daemon=True).start()

    def _build_encoding_settings(self, settings, ids: list[str]) -> None:
        from ready2rip.ui.prefs_encoding import build_encoding_settings

        build_encoding_settings(self, settings, ids)

    def _build_files_naming_settings(self, settings, default_out: str) -> None:
        from ready2rip.ui.prefs_files import build_files_naming_settings

        build_files_naming_settings(self, settings, default_out)

    def _build_ripping_settings(self, settings) -> None:
        from ready2rip.ui.prefs_ripping import build_ripping_settings

        build_ripping_settings(self, settings)

    def _build_metadata_rows(self) -> None:
        """Build Metadata page: Lookup, Artwork, Tags."""
        from ready2rip.ui.prefs_metadata import build_metadata_rows

        build_metadata_rows(self, self.store.get())

    def sync_options_from_store(self) -> None:
        """Refresh settings widgets from store (e.g. after Drive setup)."""
        from ready2rip.ui.prefs_sync import sync_options_from_store

        sync_options_from_store(self)

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
                selected = self._flac_levels.index(5) if 5 in self._flac_levels else 0
            return labels, selected
        if fmt == 'wavpack':
            labels = [label for _level, label in WAVPACK_LEVELS]
            try:
                selected = self._wavpack_levels.index(
                    normalize_wavpack_level(settings.wavpack_compression)
                )
            except ValueError:
                selected = (
                    self._wavpack_levels.index('high') if 'high' in self._wavpack_levels else 0
                )
            return labels, selected
        if fmt == 'mp3':
            labels = [label for _qid, label in MP3_QUALITIES]
            try:
                selected = self._mp3_qualities.index(normalize_mp3_quality(settings.mp3_quality))
            except ValueError:
                selected = 0
            return labels, selected
        if fmt == 'aac':
            labels = [label for _qid, label in AAC_QUALITIES]
            try:
                selected = self._aac_qualities.index(normalize_aac_quality(settings.aac_quality))
            except ValueError:
                selected = self._aac_qualities.index('cbr256')
            return labels, selected
        if fmt == 'qaac':
            labels = [label for _qid, label in QAAC_QUALITIES]
            try:
                selected = self._qaac_qualities.index(normalize_qaac_quality(settings.qaac_quality))
            except ValueError:
                selected = self._qaac_qualities.index('cvbr256')
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
        if fmt in ('', 'wav', 'aiff', 'alac'):
            # Stay in-layout (no height jump); Quality is N/A for PCM / ALAC.
            row.set_sensitive(False)
            row.set_subtitle('Not applicable')
            setattr(self, kind_attr, None)
            return
        row.set_sensitive(True)
        row.set_subtitle('')
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

    def _also_encode_format(self) -> str:
        idx = self._also_format_row.get_selected()
        if 0 <= idx < len(self._also_formats):
            return self._also_formats[idx]
        return normalize_also_encode_format(self.store.get().also_encode_format)

    def _sync_encoding_ui(self) -> None:
        self._fill_quality_combo(
            self._quality_row,
            self._primary_encode_format(),
            kind_attr='_quality_kind',
            handler=self._on_quality_changed,
        )
        also_on = bool(self._also_encode_row.get_enable_expansion())
        also_fmt = self._also_encode_format()
        folder_idx = self._also_folder_row.get_selected()
        folder_id = (
            self._also_folders[folder_idx]
            if 0 <= folder_idx < len(self._also_folders)
            else 'format_subdir'
        )
        show_also_dir = also_on and folder_id == 'custom_folder'
        # Keep the custom-folder row in layout; dim it unless Folder is Custom.
        self._also_directory_row.set_sensitive(show_also_dir)
        self._also_directory_row.set_tooltip_text(
            'Output root for the lossy encode'
            if show_also_dir
            else 'Available when Folder is Custom folder'
        )
        if also_on:
            self._fill_quality_combo(
                self._also_quality_row,
                also_fmt,
                kind_attr='_also_quality_kind',
                handler=self._on_also_quality_changed,
            )
            self._also_format_row.set_sensitive(True)
            self._also_quality_row.set_sensitive(True)
            self._also_folder_row.set_sensitive(True)
        if also_fmt == 'qaac' and not qaac_available():
            self._also_format_row.set_subtitle(
                'Requires Wine, qaac.exe, and Apple AAC codecs'
            )
        else:
            self._also_format_row.set_subtitle('')

    def _apply_quality_selection(self, fmt: str, index: int) -> None:
        if fmt == 'flac' and 0 <= index < len(self._flac_levels):
            self.store.update(flac_compression=self._flac_levels[index])
        elif fmt == 'wavpack' and 0 <= index < len(self._wavpack_levels):
            self.store.update(wavpack_compression=self._wavpack_levels[index])
        elif fmt == 'mp3' and 0 <= index < len(self._mp3_qualities):
            self.store.update(mp3_quality=self._mp3_qualities[index])
        elif fmt == 'aac' and 0 <= index < len(self._aac_qualities):
            self.store.update(aac_quality=self._aac_qualities[index])
        elif fmt == 'qaac' and 0 <= index < len(self._qaac_qualities):
            self.store.update(qaac_quality=self._qaac_qualities[index])
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

    def _bind_naming_scheme_combo(
        self,
        row: Adw.ComboRow,
        schemes: tuple[NamingScheme, ...],
        current_template: str,
        *,
        entry: Adw.EntryRow,
        custom_attr: str,
        default_template: str,
    ) -> None:
        """Wire the scheme dropdown (default display) and popup hover hints."""
        row.set_model(Gtk.StringList.new([s.label for s in schemes]))
        # Keep the default factory so the chosen preset label shows in the row.
        # list_factory only customizes popup items (tooltips).
        list_factory = Gtk.SignalListItemFactory()

        def on_setup(_factory: Gtk.SignalListItemFactory, list_item: Gtk.ListItem) -> None:
            label = Gtk.Label(xalign=0.0)
            label.set_ellipsize(Pango.EllipsizeMode.END)
            label.set_hexpand(True)
            list_item.set_child(label)

        def on_bind(_factory: Gtk.SignalListItemFactory, list_item: Gtk.ListItem) -> None:
            label = list_item.get_child()
            if not isinstance(label, Gtk.Label):
                return
            pos = int(list_item.get_position())
            if not (0 <= pos < len(schemes)):
                return
            scheme = schemes[pos]
            label.set_label(scheme.label)
            label.set_tooltip_text(scheme_tooltip(scheme))

        list_factory.connect('setup', on_setup)
        list_factory.connect('bind', on_bind)
        row.set_list_factory(list_factory)
        self._naming_scheme_factories.append(list_factory)

        self._load_naming_scheme_state(
            schemes,
            current_template,
            combo=row,
            entry=entry,
            custom_attr=custom_attr,
            default_template=default_template,
        )

    def _load_naming_scheme_state(
        self,
        schemes: tuple[NamingScheme, ...],
        template: str,
        *,
        combo: Adw.ComboRow,
        entry: Adw.EntryRow,
        custom_attr: str,
        default_template: str,
    ) -> None:
        """Select the matching preset (or Custom).

        Preset rows leave the template entry empty (hover the scheme for the
        path). Custom shows only what the user has typed.
        """
        stored = (template or '').strip()
        text = stored or default_template
        idx = scheme_index(schemes, text)
        combo.set_selected(idx)
        if schemes[idx].id == 'custom':
            # Non-preset stored value — restore into the editable field.
            setattr(self, custom_attr, stored)
            entry.set_text(stored)
        else:
            entry.set_text('')
        self._sync_naming_scheme_row_tooltip(combo, schemes)

    @staticmethod
    def _sync_naming_scheme_row_tooltip(
        row: Adw.ComboRow,
        schemes: tuple[NamingScheme, ...],
    ) -> None:
        idx = row.get_selected()
        if 0 <= idx < len(schemes):
            row.set_tooltip_text(scheme_tooltip(schemes[idx]))
        else:
            row.set_tooltip_text(None)

    def _sync_naming_entry(
        self,
        *,
        schemes: tuple[NamingScheme, ...],
        combo: Adw.ComboRow,
        entry: Adw.EntryRow,
        entry_handler,
        help_text: str,
    ) -> None:
        """Dim template entry unless Custom; keep presets blank until typed."""
        idx = combo.get_selected()
        custom = 0 <= idx < len(schemes) and schemes[idx].id == 'custom'
        entry.set_sensitive(custom)
        entry.set_tooltip_text(
            help_text if custom else 'Choose Custom… to edit this template'
        )
        if not custom and entry.get_text():
            self._with_handler_blocked(
                entry,
                entry_handler,
                lambda: entry.set_text(''),
            )
        self._sync_naming_scheme_row_tooltip(combo, schemes)

    def _sync_naming_scheme_ui(self) -> None:
        """Keep template rows in layout; editable only while Scheme is Custom…"""
        if not all(
            hasattr(self, attr)
            for attr in (
                '_folder_scheme_row',
                '_folder_template_row',
                '_filename_scheme_row',
                '_filename_row',
            )
        ):
            return
        self._sync_naming_entry(
            schemes=FOLDER_SCHEMES,
            combo=self._folder_scheme_row,
            entry=self._folder_template_row,
            entry_handler=self._on_folder_template_changed,
            help_text=FOLDER_TEMPLATE_HELP,
        )
        self._sync_naming_entry(
            schemes=FILENAME_SCHEMES,
            combo=self._filename_scheme_row,
            entry=self._filename_row,
            entry_handler=self._on_filename_changed,
            help_text=FILENAME_TEMPLATE_HELP,
        )

    @staticmethod
    def _with_handler_blocked(widget: Gtk.Widget, handler, fn) -> None:
        widget.handler_block_by_func(handler)
        try:
            fn()
        finally:
            widget.handler_unblock_by_func(handler)

    def _apply_naming_scheme_selection(
        self,
        *,
        schemes: tuple[NamingScheme, ...],
        combo: Adw.ComboRow,
        combo_handler,
        entry: Adw.EntryRow,
        entry_handler,
        store_key: str,
        custom_attr: str,
        default_template: str,
    ) -> None:
        idx = combo.get_selected()
        if not (0 <= idx < len(schemes)):
            return
        scheme = schemes[idx]

        if scheme.id == 'custom':
            # Empty until the user types (or a prior Custom value is restored).
            remembered = (getattr(self, custom_attr) or '').strip()
            self._with_handler_blocked(entry, entry_handler, lambda: entry.set_text(remembered))
            self.store.update(**{store_key: remembered or default_template})
        else:
            # Preset: save its template; leave the entry blank (tooltip has path).
            self._with_handler_blocked(entry, entry_handler, lambda: entry.set_text(''))
            self.store.update(**{store_key: scheme.template})

        # Re-assert selection so the dropdown shows the chosen preset.
        if combo.get_selected() != idx:
            self._with_handler_blocked(combo, combo_handler, lambda: combo.set_selected(idx))
        self._sync_naming_scheme_ui()

    def _apply_naming_template_edit(
        self,
        *,
        schemes: tuple[NamingScheme, ...],
        combo: Adw.ComboRow,
        text: str,
        store_key: str,
        custom_attr: str,
        default_template: str,
    ) -> None:
        """Save Custom edits; stay on Custom even if text matches a preset."""
        idx = combo.get_selected()
        on_custom = 0 <= idx < len(schemes) and schemes[idx].id == 'custom'
        if not on_custom:
            return
        setattr(self, custom_attr, text)
        self.store.update(**{store_key: text or default_template})
        self._sync_naming_scheme_ui()

    def _on_folder_scheme_changed(self, row: Adw.ComboRow, *_args) -> None:
        self._apply_naming_scheme_selection(
            schemes=FOLDER_SCHEMES,
            combo=row,
            combo_handler=self._on_folder_scheme_changed,
            entry=self._folder_template_row,
            entry_handler=self._on_folder_template_changed,
            store_key='album_folder_template',
            custom_attr='_custom_album_folder_template',
            default_template=DEFAULT_ALBUM_FOLDER_TEMPLATE,
        )

    def _on_folder_template_changed(self, row: Adw.EntryRow) -> None:
        self._apply_naming_template_edit(
            schemes=FOLDER_SCHEMES,
            combo=self._folder_scheme_row,
            text=row.get_text().strip(),
            store_key='album_folder_template',
            custom_attr='_custom_album_folder_template',
            default_template=DEFAULT_ALBUM_FOLDER_TEMPLATE,
        )

    def _on_filename_scheme_changed(self, row: Adw.ComboRow, *_args) -> None:
        self._apply_naming_scheme_selection(
            schemes=FILENAME_SCHEMES,
            combo=row,
            combo_handler=self._on_filename_scheme_changed,
            entry=self._filename_row,
            entry_handler=self._on_filename_changed,
            store_key='filename_template',
            custom_attr='_custom_filename_template',
            default_template=DEFAULT_FILENAME_TEMPLATE,
        )

    def _on_filename_changed(self, row: Adw.EntryRow) -> None:
        self._apply_naming_template_edit(
            schemes=FILENAME_SCHEMES,
            combo=self._filename_scheme_row,
            text=row.get_text().strip(),
            store_key='filename_template',
            custom_attr='_custom_filename_template',
            default_template=DEFAULT_FILENAME_TEMPLATE,
        )

    def _on_artist_style_changed(self, row: Adw.ComboRow, *_args) -> None:
        idx = row.get_selected()
        if 0 <= idx < len(ARTIST_STYLES):
            self.store.update(artist_style=ARTIST_STYLES[idx][0])

    def _on_m4a_feat_toggled(self, row: Adw.SwitchRow, *_args) -> None:
        self.store.update(m4a_feat_artists=row.get_active())

    def _on_existing_files_changed(self, row: Adw.ComboRow, *_args) -> None:
        idx = row.get_selected()
        if 0 <= idx < len(self._existing_files):
            self.store.update(existing_files=self._existing_files[idx])

    def _on_encoder_changed(self, row: Adw.ComboRow, *_args) -> None:
        idx = row.get_selected()
        if 0 <= idx < len(ENCODERS):
            fmt = ENCODERS[idx][0]
            self.store.update(encode_format=fmt)
            self._sync_encoding_ui()

    def _on_quality_changed(self, row: Adw.ComboRow, *_args) -> None:
        fmt = self._quality_kind or self._primary_encode_format()
        self._apply_quality_selection(fmt, row.get_selected())

    def _on_also_encode_toggled(self, row: Adw.ExpanderRow, *_args) -> None:
        self._apply_feature_enable(row, 'also_encode', after=lambda _on: self._sync_encoding_ui())

    def _on_also_format_changed(self, row: Adw.ComboRow, *_args) -> None:
        idx = row.get_selected()
        if 0 <= idx < len(self._also_formats):
            self.store.update(also_encode_format=self._also_formats[idx])
            self._also_quality_kind = None
            self._sync_encoding_ui()

    def _on_also_quality_changed(self, row: Adw.ComboRow, *_args) -> None:
        fmt = self._also_quality_kind or self._also_encode_format()
        self._apply_quality_selection(fmt, row.get_selected())

    def _on_also_folder_changed(self, row: Adw.ComboRow, *_args) -> None:
        idx = row.get_selected()
        if not (0 <= idx < len(self._also_folders)):
            return
        folder_id = self._also_folders[idx]
        self.store.update(also_encode_folder=folder_id)
        if folder_id == 'custom_folder':
            remembered = (self._custom_also_directory or '').strip()
            self._with_handler_blocked(
                self._also_directory_row,
                self._on_also_directory_changed,
                lambda: self._also_directory_row.set_text(remembered),
            )
            if remembered:
                self.store.update(also_encode_directory=remembered)
        # Leaving Custom: keep the remembered path so switching back restores it.
        self._sync_encoding_ui()

    def _also_folder_layout_id(self) -> str:
        idx = self._also_folder_row.get_selected()
        if 0 <= idx < len(self._also_folders):
            return self._also_folders[idx]
        return normalize_also_encode_folder(self.store.get().also_encode_folder)

    def _on_also_directory_changed(self, row: Adw.EntryRow) -> None:
        # Only persist while Custom folder is selected. Blanking the entry for
        # other layouts (or during init/sync) must not wipe the saved path.
        if self._also_folder_layout_id() != 'custom_folder':
            return
        text = row.get_text().strip()
        self._custom_also_directory = text
        self.store.update(also_encode_directory=text)

    def _on_choose_also_directory(self, *_args) -> None:
        current = (
            self._also_directory_row.get_text().strip()
            or self._custom_also_directory
            or self._output_row.get_text().strip()
            or default_output_directory()
        )

        def on_path(path: str) -> None:
            # Replace any previous path entirely (do not append / leave stale text).
            def apply() -> None:
                self._also_directory_row.set_text('')
                self._also_directory_row.set_text(path)

            self._with_handler_blocked(
                self._also_directory_row,
                self._on_also_directory_changed,
                apply,
            )
            self._custom_also_directory = path
            self.store.update(also_encode_directory=path)

        self._choose_folder('Lossy output folder', current, on_path)

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
        self._update_overread_row()
        # Re-probe when offset is set/changed (Detect path probes inside save).
        if not bool(self.store.get().drive_overread_probed):
            self._schedule_overread_probe()

    def _on_mb_toggled(self, row: Adw.SwitchRow, *_args) -> None:
        self.store.update(use_musicbrainz=row.get_active())

    def _on_freedb_toggled(self, row: Adw.SwitchRow, *_args) -> None:
        self.store.update(use_freedb=row.get_active())

    def _on_auto_lookup_toggled(self, row: Adw.ExpanderRow, *_args) -> None:
        self._apply_feature_enable(
            row,
            'auto_lookup_metadata',
            after=self._sync_lookup_source_sensitivity,
        )

    def _sync_lookup_source_sensitivity(self, lookup_enabled: bool) -> None:
        for row in (self._mb_row, self._freedb_row):
            row.set_sensitive(lookup_enabled)

    def _on_rip_mode_changed(self, row: Adw.ComboRow, *_args) -> None:
        from ready2rip.settings_defs import (
            normalize_rip_mode,
            rip_mode_subtitle,
            rip_mode_tooltip,
        )

        idx = int(row.get_selected())
        modes = getattr(self, '_rip_modes', ['archival', 'normal', 'burst'])
        mode = normalize_rip_mode(modes[idx] if 0 <= idx < len(modes) else 'archival')
        self.store.update(rip_mode=mode)
        row.set_subtitle(rip_mode_subtitle(mode))
        row.set_tooltip_text(rip_mode_tooltip(mode))
        self._sync_rip_mode_sensitivity(mode)

    def _on_htoa_toggled(self, row: Adw.SwitchRow, *_args) -> None:
        self.store.update(rip_htoa=row.get_active())

    def _on_data_tracks_toggled(self, row: Adw.SwitchRow, *_args) -> None:
        self.store.update(rip_data_tracks=row.get_active())

    def _on_copy_image_toggled(self, row: Adw.SwitchRow, *_args) -> None:
        self.store.update(copy_image=row.get_active())
        if row.get_active():
            self._toast('Copy image on — rip writes one continuous disc image')

    def _on_rg_toggled(self, row: Adw.SwitchRow, *_args) -> None:
        self.store.update(apply_replaygain=row.get_active())

    def _on_sound_check_toggled(self, row: Adw.SwitchRow, *_args) -> None:
        self.store.update(write_sound_check=row.get_active())

    def _on_ripper_tag_toggled(self, row: Adw.SwitchRow, *_args) -> None:
        self.store.update(add_ripper_tag=row.get_active())

    def _on_fetch_lyrics_toggled(self, row: Adw.SwitchRow, *_args) -> None:
        enabled = bool(row.get_active())
        self.store.update(fetch_lyrics=enabled)
        if (
            enabled
            and self._album is not None
            and self._meta_cache.has_useful_metadata(self._album)
        ):
            self._start_lyrics_fetch(self._album)

    def _on_embed_lyrics_toggled(self, row: Adw.ExpanderRow, *_args) -> None:
        self._apply_feature_enable(row, 'embed_lyrics')

    def _on_lyrics_mode_changed(self, row: Adw.ComboRow, *_args) -> None:
        idx = row.get_selected()
        if 0 <= idx < len(self._lyrics_save_modes):
            self.store.update(lyrics_save_mode=self._lyrics_save_modes[idx])

    def _on_ar_toggled(self, row: Adw.SwitchRow, *_args) -> None:
        enabled = bool(row.get_active())
        self.store.update(verify_accuraterip=enabled)
        self._sync_require_db_match_sensitivity(enabled)

    def _on_require_db_match_toggled(self, row: Adw.SwitchRow, *_args) -> None:
        self.store.update(require_db_match=row.get_active())

    def _on_archival_strict_toggled(self, row: Adw.SwitchRow, *_args) -> None:
        self.store.update(archival_strict=row.get_active())

    def _sync_require_db_match_sensitivity(self, verify_ar: bool) -> None:
        self._require_db_match_row.set_sensitive(bool(verify_ar))

    def _on_ctdb_toggled(self, row: Adw.SwitchRow, *_args) -> None:
        self.store.update(verify_ctdb=row.get_active())

    def _on_submit_ctdb_toggled(self, row: Adw.SwitchRow, *_args) -> None:
        self.store.update(submit_ctdb=row.get_active())

    def _sync_rip_mode_sensitivity(self, rip_mode: str) -> None:
        # Burst fallback only applies to Archival secure retries.
        self._burst_row.set_sensitive(rip_mode == 'archival')

    def _on_burst_toggled(self, row: Adw.SwitchRow, *_args) -> None:
        self.store.update(burst_fallback=row.get_active())

    def _on_log_toggled(self, row: Adw.SwitchRow, *_args) -> None:
        self.store.update(write_rip_log=row.get_active())

    def _on_cue_toggled(self, row: Adw.SwitchRow, *_args) -> None:
        self.store.update(write_cue_file=row.get_active())
        if row.get_active():
            self._toast('Write CUE on — sheet saved with the rip')

    def _on_auto_rip_toggled(self, row: Adw.SwitchRow, *_args) -> None:
        self.store.update(auto_rip=row.get_active())
        if row.get_active():
            self._toast('Auto-rip on — insert a disc to start')
        else:
            self._auto_rip_pending = False

    def _on_auto_eject_toggled(self, row: Adw.SwitchRow, *_args) -> None:
        self.store.update(auto_eject=row.get_active())

    def _after_embed_toggle(self, _enabled: bool) -> None:
        if self.store.get().wants_embed_artwork() and self._artwork is not None:
            self._prepare_embed_artwork()
        self._update_cover_size_label()

    def _on_embed_lossy_toggled(self, row: Adw.ExpanderRow, *_args) -> None:
        self._apply_feature_enable(row, 'embed_artwork', after=self._after_embed_toggle)

    def _on_embed_lossless_toggled(self, row: Adw.ExpanderRow, *_args) -> None:
        self._apply_feature_enable(row, 'embed_artwork_lossless', after=self._after_embed_toggle)

    def _select_art_size_row(
        self,
        row: Adw.ComboRow,
        size: int,
        *,
        values: list[int] | None = None,
    ) -> None:
        opts = values or getattr(self, '_art_size_values_lossy', None) or [
            px for px, _label in ARTWORK_SIZES
        ]
        try:
            row.set_selected(opts.index(int(size)))
        except ValueError:
            try:
                row.set_selected(opts.index(DEFAULT_ARTWORK_MAX_SIZE))
            except ValueError:
                row.set_selected(max(0, len(opts) - 1))

    def _on_fetch_art_toggled(self, row: Adw.ExpanderRow, *_args) -> None:
        def after(enabled: bool) -> None:
            self._sync_artwork_source_sensitivity(enabled)
            if enabled and self._album is not None and self._artwork is None:
                self._start_artwork_fetch(self._album)

        self._apply_feature_enable(row, 'fetch_artwork', after=after)

    def _on_art_itunes_toggled(self, row: Adw.SwitchRow, *_args) -> None:
        self.store.update(artwork_source_itunes=row.get_active())
        self._maybe_refetch_artwork()

    def _on_art_caa_toggled(self, row: Adw.SwitchRow, *_args) -> None:
        self.store.update(artwork_source_caa=row.get_active())
        self._maybe_refetch_artwork()

    def _sync_artwork_source_sensitivity(self, fetch_enabled: bool) -> None:
        for row in (self._art_caa_row, self._art_itunes_row):
            row.set_sensitive(fetch_enabled)

    def _on_art_size_lossy_changed(self, row: Adw.ComboRow, *_args) -> None:
        idx = row.get_selected()
        values = self._art_size_values_lossy
        if 0 <= idx < len(values):
            self.store.update(artwork_max_size=values[idx])
            self._refresh_embed_after_size_change(kind='lossy')

    def _on_art_size_lossless_changed(self, row: Adw.ComboRow, *_args) -> None:
        idx = row.get_selected()
        values = self._art_size_values_lossless
        if 0 <= idx < len(values):
            self.store.update(artwork_max_size_lossless=values[idx])
            self._refresh_embed_after_size_change(kind='lossless')

    def _refresh_embed_after_size_change(self, *, kind: str) -> None:
        if self._artwork is None:
            return
        self._prepare_embed_artwork()
        self._update_cover_size_label()
        emb = self._artwork_embed if kind == 'lossy' else self._artwork_embed_lossless
        if emb is not None:
            self._toast(
                f'{kind.capitalize()} embed size set to {emb.width}×{emb.height} '
                f'(from {self._artwork.width}×{self._artwork.height} source)'
            )

    # —— Header actions ——

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

    # —— Drive monitor / disc load ——

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
        """Toast label for where album metadata came from."""
        raw = (source or '').strip().lower()
        if not raw or raw == 'manual':
            return 'No metadata'
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
        """Refresh window subtitle and cover placeholder."""
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
            self.window_title.set_subtitle(f'{n} track' if n == 1 else f'{n} tracks')
        else:
            self.window_title.set_subtitle('')
        if self._artwork is None:
            self._show_placeholder_cover()

    def _build_album_edit_rows(self) -> None:
        from ready2rip.ui.album_edit import build_album_edit_rows

        build_album_edit_rows(self)

    def _set_album_fields_editable(self, editable: bool) -> None:
        """Lock Album EntryRows while ripping without greying their text."""
        for row in self._album_field_rows:
            row.set_editable(editable)
            if editable:
                row.remove_css_class('ready2rip-album-locked')
            else:
                row.add_css_class('ready2rip-album-locked')

    def _blank_album_for_disc(self, info: DiscInfo) -> AlbumMetadata:
        tracks = [TrackMetadata(number=t.number, title='', artist='') for t in info.tracks]
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
                    new_tracks.append(TrackMetadata(number=toc.number, title='', artist=''))
            album.tracks = new_tracks
        return album

    def _fill_album_edit_fields(self, album: AlbumMetadata | None) -> None:
        self._suppress_meta_write = True
        try:
            if self._album_title_row is not None:
                self._album_title_row.set_text(album.title if album else '')
            if self._album_artist_row is not None:
                self._album_artist_row.set_text(normalize_artists(album.artist) if album else '')
            if self._album_date_row is not None:
                self._album_date_row.set_text(normalize_release_date(album.date) if album else '')
            if self._album_genre_row is not None:
                self._album_genre_row.set_text(album.genre if album else '')
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
        if self._album_disc_row is not None:
            disc_num, disc_total = parse_disc_field(self._album_disc_row.get_text())
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
        mark_album_edited(album)
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
        from ready2rip.ui.album_edit import rebuild_track_list

        rebuild_track_list(self, info)

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

        for num in self._track_title_entries:
            title_label = self._track_title_labels.get(num)
            artist_label = self._track_artist_labels.get(num)
            title_entry = self._track_title_entries.get(num)
            artist_entry = self._track_artist_entries.get(num)
            if title_label is not None and title_entry is not None:
                title_label.set_visible(not editing)
                title_entry.set_visible(editing)
            if artist_label is not None and artist_entry is not None:
                artist_label.set_visible(not editing)
                artist_entry.set_visible(editing)

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
        artist = normalize_artists(artist_entry.get_text()) if artist_entry else ''

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

        mark_album_edited(album)
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
            mark_album_edited(album)
            self._sync_track_labels_from_entries()
            self._schedule_cache_save()
            self._toast(f'Applied album artist to {filled} track{"s" if filled != 1 else ""}')
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

    def _refresh_track_fields_from_album(self) -> None:
        """Update existing track rows in place (avoids full list rebuild)."""
        album = self._album
        if album is None:
            return
        album_artist = normalize_artists(album.artist or '')
        useful = self._meta_cache.has_useful_metadata(album)
        self._suppress_meta_write = True
        try:
            for num, title_entry in self._track_title_entries.items():
                meta = track_meta_for(album, num)
                title = meta.title if meta else ''
                artist = normalize_artists((meta.artist if meta else '') or '')
                if not artist and album_artist and useful:
                    artist = album_artist
                title_entry.set_text(title)
                artist_entry = self._track_artist_entries.get(num)
                if artist_entry is not None:
                    artist_entry.set_text(artist)
            self._sync_track_labels_from_entries()
        finally:
            self._suppress_meta_write = False

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
