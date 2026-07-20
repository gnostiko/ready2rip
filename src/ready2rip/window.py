# SPDX-License-Identifier: GPL-3.0-or-later
"""Main application window."""

from __future__ import annotations

import threading

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

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
    lookup_metadata,
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


@Gtk.Template(string="""
<?xml version="1.0" encoding="UTF-8"?>
<interface>
  <requires lib="gtk" version="4.0"/>
  <requires lib="Adw" version="1.0"/>
  <template class="Ready2RipWindow" parent="AdwApplicationWindow">
    <property name="title"></property>
    <property name="icon-name">org.ready2rip.Ready2Rip</property>
    <property name="default-width">1040</property>
    <property name="default-height">720</property>
    <child>
      <object class="AdwToolbarView">
        <child type="top">
          <object class="AdwHeaderBar" id="header_bar">
            <property name="show-title">false</property>
            <child type="start">
              <object class="GtkToggleButton" id="sidebar_button">
                <property name="icon-name">sidebar-show-symbolic</property>
                <property name="tooltip-text" translatable="yes">Toggle sidebar</property>
                <property name="active">true</property>
                <signal name="toggled" handler="_on_sidebar_toggled"/>
              </object>
            </child>
            <child type="start">
              <object class="GtkButton" id="about_button">
                <property name="icon-name">help-about-symbolic</property>
                <property name="tooltip-text" translatable="yes">About ready2rip</property>
                <property name="action-name">app.about</property>
              </object>
            </child>
            <!-- type=end packs right-to-left: first child is outermost (right). -->
            <child type="end">
              <object class="GtkButton" id="eject_button">
                <property name="label" translatable="yes">Eject</property>
                <property name="tooltip-text" translatable="yes">Eject the disc / open the tray</property>
                <style>
                  <class name="destructive-action"/>
                  <class name="pill"/>
                </style>
                <signal name="clicked" handler="_on_eject_clicked"/>
              </object>
            </child>
            <child type="end">
              <object class="GtkButton" id="rip_button">
                <property name="label" translatable="yes">Rip CD</property>
                <property name="sensitive">false</property>
                <property name="tooltip-text" translatable="yes">Rip the inserted CD</property>
                <style>
                  <class name="suggested-action"/>
                  <class name="pill"/>
                </style>
                <signal name="clicked" handler="_on_rip_clicked"/>
              </object>
            </child>
            <child type="end">
              <object class="GtkButton" id="lookup_button">
                <property name="label" translatable="yes">Lookup</property>
                <property name="sensitive">false</property>
                <property name="tooltip-text" translatable="yes">Look up metadata online</property>
                <signal name="clicked" handler="_on_lookup_clicked"/>
              </object>
            </child>
          </object>
        </child>
        <child>
          <object class="AdwToastOverlay" id="toast_overlay">
            <child>
              <!-- Collapsible GNOME sidebar: rip options + disc info -->
              <object class="AdwBreakpointBin" id="breakpoint_bin">
                <property name="width-request">360</property>
                <property name="height-request">200</property>
                <child>
                  <object class="AdwOverlaySplitView" id="split_view">
                    <property name="vexpand">true</property>
                    <property name="hexpand">true</property>
                    <property name="sidebar-position">start</property>
                    <property name="show-sidebar">true</property>
                    <property name="collapsed">false</property>
                    <property name="min-sidebar-width">280</property>
                    <property name="max-sidebar-width">340</property>
                    <property name="enable-hide-gesture">true</property>
                    <property name="enable-show-gesture">true</property>
                    <child type="sidebar">
                      <object class="GtkScrolledWindow" id="sidebar_scroll">
                        <property name="hscrollbar-policy">never</property>
                        <property name="vexpand">true</property>
                        <property name="hexpand">true</property>
                        <style>
                          <class name="background"/>
                        </style>
                        <child>
                          <object class="GtkBox" id="sidebar_box">
                            <property name="orientation">vertical</property>
                            <property name="spacing">18</property>
                            <property name="margin-top">12</property>
                            <property name="margin-bottom">24</property>
                            <property name="margin-start">12</property>
                            <property name="margin-end">12</property>
                            <child>
                              <object class="GtkLabel">
                                <property name="label" translatable="yes">Settings</property>
                                <property name="xalign">0</property>
                                <property name="margin-bottom">6</property>
                                <style>
                                  <class name="title-4"/>
                                </style>
                              </object>
                            </child>
                            <child>
                              <object class="AdwPreferencesGroup" id="calibration_group">
                                <property name="title" translatable="yes">Calibration</property>
                              </object>
                            </child>
                            <child>
                              <object class="AdwPreferencesGroup" id="options_group">
                                <property name="title" translatable="yes">Rip options</property>
                              </object>
                            </child>
                            <child>
                              <object class="AdwPreferencesGroup" id="metadata_group">
                                <property name="title" translatable="yes">Metadata options</property>
                              </object>
                            </child>
                            <child>
                              <object class="GtkLabel">
                                <property name="label" translatable="yes">Technical</property>
                                <property name="xalign">0</property>
                                <property name="margin-top">6</property>
                                <property name="margin-bottom">6</property>
                                <style>
                                  <class name="title-4"/>
                                </style>
                              </object>
                            </child>
                            <child>
                              <object class="AdwPreferencesGroup" id="disc_group">
                                <property name="title" translatable="yes">Disc</property>
                              </object>
                            </child>
                            <child>
                              <object class="AdwPreferencesGroup" id="drive_group">
                                <property name="title" translatable="yes">Drive</property>
                              </object>
                            </child>
                          </object>
                        </child>
                      </object>
                    </child>
                    <child>
                      <object class="GtkStack" id="stack">
                        <property name="vexpand">true</property>
                        <property name="hexpand">true</property>
                        <property name="transition-type">crossfade</property>
                        <child>
                          <object class="GtkStackPage">
                            <property name="name">empty</property>
                            <property name="child">
                              <object class="AdwStatusPage" id="status_page">
                                <property name="icon-name">media-optical-symbolic</property>
                                <property name="title" translatable="yes">No disc detected</property>
                                <property name="description" translatable="yes">
                                  Insert an audio CD and press Refresh.
                                </property>
                              </object>
                            </property>
                          </object>
                        </child>
                        <child>
                          <object class="GtkStackPage">
                            <property name="name">disc</property>
                            <property name="child">
                              <object class="GtkScrolledWindow">
                                <property name="vexpand">true</property>
                                <property name="hexpand">true</property>
                                <property name="hscrollbar-policy">never</property>
                                <property name="propagate-natural-height">false</property>
                                <child>
                                  <object class="AdwClamp">
                                    <property name="maximum-size">680</property>
                                    <property name="tightening-threshold">400</property>
                                    <property name="unit">sp</property>
                                    <child>
                                      <object class="GtkBox" id="content_box">
                                        <property name="orientation">vertical</property>
                                        <property name="spacing">24</property>
                                        <property name="margin-top">12</property>
                                        <property name="margin-bottom">24</property>
                                        <property name="margin-start">12</property>
                                        <property name="margin-end">12</property>

                                        <!-- Album header: large centered cover (GNOME album style) -->
                                        <child>
                                          <object class="GtkBox" id="album_box">
                                            <property name="orientation">vertical</property>
                                            <property name="spacing">12</property>
                                            <property name="halign">center</property>
                                            <property name="hexpand">true</property>
                                            <child>
                                              <object class="GtkBox" id="cover_frame">
                                                <property name="orientation">vertical</property>
                                                <property name="halign">center</property>
                                                <property name="valign">center</property>
                                                <property name="hexpand">false</property>
                                                <property name="vexpand">false</property>
                                                <property name="width-request">240</property>
                                                <property name="height-request">240</property>
                                                <property name="overflow">hidden</property>
                                                <style>
                                                  <class name="card"/>
                                                  <class name="ready2rip-cover-frame"/>
                                                </style>
                                                <child>
                                                  <object class="GtkOverlay" id="cover_overlay">
                                                    <property name="hexpand">true</property>
                                                    <property name="vexpand">true</property>
                                                    <property name="halign">fill</property>
                                                    <property name="valign">fill</property>
                                                    <property name="overflow">hidden</property>
                                                    <child>
                                                      <object class="GtkPicture" id="cover_picture">
                                                        <property name="hexpand">true</property>
                                                        <property name="vexpand">true</property>
                                                        <property name="halign">fill</property>
                                                        <property name="valign">fill</property>
                                                        <property name="can-shrink">true</property>
                                                        <property name="content-fit">cover</property>
                                                        <property name="tooltip-text" translatable="yes">Album artwork</property>
                                                        <style>
                                                          <class name="ready2rip-cover-picture"/>
                                                        </style>
                                                      </object>
                                                    </child>
                                                    <child type="overlay">
                                                      <object class="GtkImage" id="cover_placeholder">
                                                        <property name="icon-name">folder-music-symbolic</property>
                                                        <property name="pixel-size">96</property>
                                                        <property name="halign">center</property>
                                                        <property name="valign">center</property>
                                                        <property name="can-target">false</property>
                                                        <property name="tooltip-text" translatable="yes">Album artwork</property>
                                                        <style>
                                                          <class name="ready2rip-cover-placeholder"/>
                                                        </style>
                                                      </object>
                                                    </child>
                                                    <child type="overlay">
                                                      <object class="GtkBox" id="cover_actions">
                                                        <property name="orientation">horizontal</property>
                                                        <property name="spacing">10</property>
                                                        <property name="halign">center</property>
                                                        <property name="valign">center</property>
                                                        <property name="opacity">0</property>
                                                        <child>
                                                          <object class="GtkButton" id="search_art_button">
                                                            <property name="icon-name">system-search-symbolic</property>
                                                            <property name="valign">center</property>
                                                            <property name="can-focus">true</property>
                                                            <property name="tooltip-text" translatable="yes">Search for artwork</property>
                                                            <style>
                                                              <class name="circular"/>
                                                              <class name="osd"/>
                                                              <class name="ready2rip-cover-button"/>
                                                            </style>
                                                            <signal name="clicked" handler="_on_search_art_clicked"/>
                                                          </object>
                                                        </child>
                                                        <child>
                                                          <object class="GtkButton" id="choose_art_button">
                                                            <property name="icon-name">folder-symbolic</property>
                                                            <property name="valign">center</property>
                                                            <property name="can-focus">true</property>
                                                            <property name="tooltip-text" translatable="yes">Choose image from file</property>
                                                            <style>
                                                              <class name="circular"/>
                                                              <class name="osd"/>
                                                              <class name="ready2rip-cover-button"/>
                                                            </style>
                                                            <signal name="clicked" handler="_on_choose_art_clicked"/>
                                                          </object>
                                                        </child>
                                                        <child>
                                                          <object class="GtkButton" id="clear_art_button">
                                                            <property name="icon-name">user-trash-symbolic</property>
                                                            <property name="valign">center</property>
                                                            <property name="can-focus">true</property>
                                                            <property name="tooltip-text" translatable="yes">Remove artwork</property>
                                                            <style>
                                                              <class name="circular"/>
                                                              <class name="osd"/>
                                                              <class name="destructive-action"/>
                                                              <class name="ready2rip-cover-button"/>
                                                              <class name="ready2rip-cover-trash"/>
                                                            </style>
                                                            <signal name="clicked" handler="_on_clear_art_clicked"/>
                                                          </object>
                                                        </child>
                                                      </object>
                                                    </child>
                                                  </object>
                                                </child>
                                              </object>
                                            </child>
                                            <child>
                                              <object class="GtkBox">
                                                <property name="orientation">vertical</property>
                                                <property name="spacing">4</property>
                                                <property name="halign">center</property>
                                                <property name="hexpand">true</property>
                                                <child>
                                                  <object class="GtkLabel" id="album_title_label">
                                                    <property name="label">Unknown Album</property>
                                                    <property name="xalign">0.5</property>
                                                    <property name="justify">center</property>
                                                    <property name="wrap">true</property>
                                                    <property name="wrap-mode">word-char</property>
                                                    <property name="ellipsize">end</property>
                                                    <property name="lines">2</property>
                                                    <property name="max-width-chars">40</property>
                                                    <property name="selectable">false</property>
                                                    <style>
                                                      <class name="title-1"/>
                                                    </style>
                                                  </object>
                                                </child>
                                                <child>
                                                  <object class="GtkLabel" id="album_artist_label">
                                                    <property name="label">Unknown Artist</property>
                                                    <property name="xalign">0.5</property>
                                                    <property name="justify">center</property>
                                                    <property name="wrap">true</property>
                                                    <property name="ellipsize">end</property>
                                                    <property name="max-width-chars">40</property>
                                                    <property name="selectable">false</property>
                                                    <style>
                                                      <class name="title-3"/>
                                                      <class name="dim-label"/>
                                                    </style>
                                                  </object>
                                                </child>
                                                <child>
                                                  <object class="GtkLabel" id="album_meta_label">
                                                    <property name="label"></property>
                                                    <property name="xalign">0.5</property>
                                                    <property name="justify">center</property>
                                                    <property name="wrap">true</property>
                                                    <property name="ellipsize">end</property>
                                                    <property name="selectable">false</property>
                                                    <style>
                                                      <class name="body"/>
                                                      <class name="dim-label"/>
                                                    </style>
                                                  </object>
                                                </child>
                                                <child>
                                                  <object class="GtkLabel" id="metadata_source_label">
                                                    <property name="label">Metadata: not looked up yet</property>
                                                    <property name="xalign">0.5</property>
                                                    <property name="justify">center</property>
                                                    <property name="wrap">true</property>
                                                    <property name="ellipsize">end</property>
                                                    <property name="selectable">false</property>
                                                    <style>
                                                      <class name="caption"/>
                                                      <class name="dim-label"/>
                                                    </style>
                                                  </object>
                                                </child>
                                                <child>
                                                  <object class="GtkProgressBar" id="lookup_progress">
                                                    <property name="visible">false</property>
                                                    <property name="pulse-step">0.2</property>
                                                    <property name="margin-top">6</property>
                                                    <property name="halign">center</property>
                                                    <property name="width-request">200</property>
                                                  </object>
                                                </child>
                                              </object>
                                            </child>
                                          </object>
                                        </child>

                                        <child>
                                          <object class="AdwPreferencesGroup" id="album_edit_group">
                                            <property name="title" translatable="yes">Album</property>
                                          </object>
                                        </child>

                                        <child>
                                          <object class="AdwPreferencesGroup" id="track_edit_group">
                                            <property name="title" translatable="yes">Tracks</property>
                                          </object>
                                        </child>

                                      </object>
                                    </child>
                                  </object>
                                </child>
                              </object>
                            </property>
                          </object>
                        </child>
                      </object>
                    </child>
                  </object>
                </child>
              </object>
            </child>
          </object>
        </child>
        <!-- Bottom slide-up progress panel (GNOME-style) -->
        <child type="bottom">
          <object class="GtkRevealer" id="rip_revealer">
            <property name="reveal-child">false</property>
            <property name="transition-type">slide-up</property>
            <property name="transition-duration">220</property>
            <child>
              <object class="GtkBox" id="rip_banner">
                <property name="orientation">vertical</property>
                <property name="spacing">8</property>
                <property name="margin-start">16</property>
                <property name="margin-end">16</property>
                <property name="margin-top">12</property>
                <property name="margin-bottom">16</property>
                <style>
                  <class name="ready2rip-rip-bar"/>
                </style>
                <child>
                  <object class="GtkLabel" id="rip_title_label">
                    <property name="label" translatable="yes">Ripping</property>
                    <property name="xalign">0</property>
                    <property name="halign">start</property>
                    <property name="selectable">false</property>
                    <style>
                      <class name="heading"/>
                    </style>
                  </object>
                </child>
                <child>
                  <object class="GtkLabel" id="rip_status_label">
                    <property name="label">Preparing…</property>
                    <property name="xalign">0</property>
                    <property name="wrap">true</property>
                    <property name="wrap-mode">word-char</property>
                    <property name="ellipsize">end</property>
                    <property name="selectable">false</property>
                    <style>
                      <class name="body"/>
                      <class name="dim-label"/>
                    </style>
                  </object>
                </child>
                <child>
                  <object class="GtkProgressBar" id="rip_progress">
                    <property name="show-text">false</property>
                    <property name="fraction">0</property>
                    <property name="hexpand">true</property>
                    <style>
                      <class name="ready2rip-progress"/>
                    </style>
                  </object>
                </child>
                <child>
                  <object class="GtkLabel" id="rip_percent_label">
                    <property name="label">0%</property>
                    <property name="xalign">1</property>
                    <property name="halign">end</property>
                    <property name="selectable">false</property>
                    <style>
                      <class name="caption"/>
                      <class name="dim-label"/>
                    </style>
                  </object>
                </child>
              </object>
            </child>
          </object>
        </child>
      </object>
    </child>
  </template>
</interface>
""")
class Ready2RipWindow(Adw.ApplicationWindow):
    __gtype_name__ = 'Ready2RipWindow'

    toast_overlay = Gtk.Template.Child()
    breakpoint_bin = Gtk.Template.Child()
    split_view = Gtk.Template.Child()
    sidebar_button = Gtk.Template.Child()
    status_page = Gtk.Template.Child()
    stack = Gtk.Template.Child()
    track_edit_group = Gtk.Template.Child()
    album_edit_group = Gtk.Template.Child()
    calibration_group = Gtk.Template.Child()
    disc_group = Gtk.Template.Child()
    drive_group = Gtk.Template.Child()
    options_group = Gtk.Template.Child()
    metadata_group = Gtk.Template.Child()
    rip_button = Gtk.Template.Child()
    lookup_button = Gtk.Template.Child()
    eject_button = Gtk.Template.Child()
    album_title_label = Gtk.Template.Child()
    album_artist_label = Gtk.Template.Child()
    album_meta_label = Gtk.Template.Child()
    metadata_source_label = Gtk.Template.Child()
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

    _COVER_SIZE = 240
    # Smaller symbolic icon looks better than filling the whole frame.
    _COVER_PLACEHOLDER_ICON_SIZE = 96

    def __init__(self, store: SettingsStore | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self._disc: DiscInfo | None = None
        self._ids: DiscIdentifiers | None = None
        self._album: AlbumMetadata | None = None
        self._track_rows: list[Gtk.Widget] = []
        self._track_checks: dict[int, Gtk.CheckButton] = {}
        self._track_title_rows: dict[int, Adw.EntryRow] = {}
        self._track_artist_rows: dict[int, Adw.EntryRow] = {}
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
        # Disc identity last auto-ripped / loaded (avoid re-ripping same media).
        self._auto_rip_disc_key: str | None = None
        self._auto_rip_pending = False
        self._poll_id: int | None = None

        self.store = store if store is not None else SettingsStore()
        self._device = self.store.get().device

        self._setup_split_view()
        self._setup_cover_widget()
        self._setup_progress_styles()
        self._build_calibration_row()
        self._build_options_rows()
        self._build_metadata_rows()
        self._build_album_edit_rows()
        self._rebuild_drive_rows(self.store.get().device or '/dev/sr0')
        self._refresh_disc()
        self._start_drive_monitor()

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
        """CSS for large cover frame and bottom progress bar (incl. success green)."""
        size = self._COVER_SIZE
        css = Gtk.CssProvider()
        css.load_from_string(
            f"""
            .ready2rip-cover-frame {{
                min-width: {size}px;
                min-height: {size}px;
                border-radius: 12px;
            }}
            /* Dimmed, compact music glyph when no cover is loaded */
            .ready2rip-cover-placeholder {{
                opacity: 0.4;
                color: @dim_label_color;
            }}
            .ready2rip-rip-bar {{
                background-color: @window_bg_color;
            }}
            progressbar.ready2rip-progress {{
                min-height: 6px;
            }}
            progressbar.ready2rip-progress > trough {{
                min-height: 6px;
                border-radius: 9999px;
            }}
            progressbar.ready2rip-progress > trough > progress {{
                min-height: 6px;
                border-radius: 9999px;
            }}
            progressbar.ready2rip-progress.success > trough > progress {{
                background-color: @success_color;
            }}
            progressbar.ready2rip-progress.error > trough > progress {{
                background-color: @error_color;
            }}
            /* Compact recalibrate control: muted icon button */
            button.ready2rip-setup-icon {{
                opacity: 0.65;
            }}
            button.ready2rip-setup-icon:hover {{
                opacity: 1.0;
            }}
            /* GNOME circular OSD cover actions (Adwaita floating toolbar style) */
            button.ready2rip-cover-button {{
                min-width: 40px;
                min-height: 40px;
                padding: 0;
                border-radius: 9999px;
            }}
            button.ready2rip-cover-button.ready2rip-cover-trash,
            button.ready2rip-cover-button.ready2rip-cover-trash:hover,
            button.ready2rip-cover-button.ready2rip-cover-trash:active {{
                background-color: @destructive_bg_color;
                color: @destructive_fg_color;
                border: none;
                box-shadow: none;
            }}
            button.ready2rip-cover-button.ready2rip-cover-trash:hover {{
                filter: brightness(1.08);
            }}
            button.ready2rip-cover-button.ready2rip-cover-trash:disabled {{
                opacity: 0.45;
            }}
            """
        )
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(),
            css,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

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
        """Clear picture and show a compact, dimmed music icon."""
        size = self._COVER_SIZE
        self.cover_picture.set_paintable(None)
        self.cover_picture.set_size_request(size, size)
        self.cover_overlay.set_size_request(size, size)
        self.cover_frame.set_size_request(size, size)

        icon = self._COVER_PLACEHOLDER_ICON_SIZE
        self.cover_placeholder.set_from_icon_name('folder-music-symbolic')
        self.cover_placeholder.set_pixel_size(icon)
        self.cover_placeholder.set_visible(True)

    # —— Sidebar groups ——

    def _build_calibration_row(self) -> None:
        """Top-of-sidebar Calibration group with Drive setup action."""
        self._setup_row = Adw.ActionRow(
            title='Drive setup',
            activatable=True,
        )
        # Status checkmark when calibrated (GNOME: symbolic + success).
        self._setup_ok_icon = Gtk.Image.new_from_icon_name('emblem-ok-symbolic')
        self._setup_ok_icon.set_valign(Gtk.Align.CENTER)
        self._setup_ok_icon.add_css_class('success')
        self._setup_ok_icon.set_visible(False)
        self._setup_row.add_suffix(self._setup_ok_icon)

        # Action: text "Run setup", or compact grey optical icon when complete.
        self._setup_btn = Gtk.Button(label='Run setup')
        self._setup_btn.set_valign(Gtk.Align.CENTER)
        self._setup_btn.connect('clicked', self._on_run_drive_setup)
        self._setup_row.add_suffix(self._setup_btn)
        self._setup_row.set_activatable_widget(self._setup_btn)
        self.calibration_group.add(self._setup_row)
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
            row.set_title('Drive calibrated')
            row.set_subtitle(f'Offset {offset} · {device}')
            self._setup_ok_icon.set_visible(True)
            # Small grey media-optical icon; tooltip carries "Recalibrate".
            btn.set_icon_name('media-optical-symbolic')
            btn.add_css_class('flat')
            btn.add_css_class('circular')
            btn.add_css_class('ready2rip-setup-icon')
            btn.set_tooltip_text('Recalibrate')
        else:
            row.set_title('Drive setup')
            row.set_subtitle('Measure sample offset, cache, Accurate Stream, and C2')
            self._setup_ok_icon.set_visible(False)
            # Restore text button (clear icon-only child if present).
            btn.set_icon_name('')
            btn.set_label('Run setup')
            btn.add_css_class('suggested-action')
            btn.set_tooltip_text('Run drive calibration')

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
        self._device_row = Adw.EntryRow(title='Optical device path')
        self._device_row.set_text(settings.device or '/dev/sr0')
        self._device_row.set_tooltip_text('e.g. /dev/sr0')
        self._device_row.connect('changed', self._on_device_path_changed)
        self._add_option_row(self._device_row)

        status = (
            f'Configured for {settings.drive_offset_device or "this drive"}'
            if settings.drive_offset_configured
            else 'Not calibrated — run Drive setup in the Drive panel'
        )
        self._offset_row = Adw.SpinRow(
            title='Drive sample offset',
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
        self._output_row = Adw.EntryRow(title='Output folder')
        self._output_row.set_text(settings.output_directory or default_out)
        self._output_row.set_tooltip_text(f'Default: {default_out}')
        self._output_row.connect('changed', self._on_output_changed)
        self._add_option_row(self._output_row)

        self._folder_template_row = Adw.EntryRow(title='Album folder template')
        self._folder_template_row.set_text(settings.album_folder_template)
        self._folder_template_row.set_tooltip_text(
            '{album_artist}/{album}/{disc_folder} · also {year}, {disc}, {totaldiscs}'
        )
        self._folder_template_row.connect(
            'changed', self._on_folder_template_changed
        )
        self._add_option_row(self._folder_template_row)

        self._filename_row = Adw.EntryRow(title='Track filename template')
        self._filename_row.set_text(settings.filename_template)
        self._filename_row.set_tooltip_text(
            '{track:02d} - {title} · also {artist}, {album}, {disc}, {totaldiscs}'
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

        self._htoa_row = Adw.SwitchRow(
            title='Hidden track (HTOA)',
            subtitle='Save non-silent pregap as track 00',
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
            subtitle='Re-rip with -Z if secure mode fails',
            active=settings.burst_fallback,
        )
        self._burst_row.connect('notify::active', self._on_burst_toggled)
        self._add_option_row(self._burst_row)

        self._log_row = Adw.SwitchRow(
            title='Write rip log',
            subtitle='EAC-style status log in album folder',
            active=settings.write_rip_log,
        )
        self._log_row.connect('notify::active', self._on_log_toggled)
        self._add_option_row(self._log_row)

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

        # Download artwork: expander with per-source toggles nested inside.
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

        self._add_metadata_row(self._fetch_art_row)
        self._sync_artwork_source_sensitivity(settings.fetch_artwork)

        self._embed_art_row = Adw.ExpanderRow(
            title='Embed artwork',
            subtitle='Write cover image into ripped files',
        )
        self._embed_art_row.set_show_enable_switch(True)
        self._embed_art_row.set_enable_expansion(settings.embed_artwork)
        self._embed_art_row.set_expanded(settings.embed_artwork)
        self._embed_art_row.connect(
            'notify::enable-expansion', self._on_embed_art_toggled
        )

        self._art_size_row = Adw.ComboRow(title='Size')
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
        self._embed_art_row.add_row(self._art_size_row)
        self._add_metadata_row(self._embed_art_row)

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
            (self._ar_row, self._on_ar_toggled),
            (self._burst_row, self._on_burst_toggled),
            (self._log_row, self._on_log_toggled),
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
            self._ar_row.set_active(settings.verify_accuraterip)
            self._burst_row.set_active(settings.burst_fallback)
            self._log_row.set_active(settings.write_rip_log)
            self._auto_rip_row.set_active(settings.auto_rip)
            self._auto_eject_row.set_active(settings.auto_eject)
            self._auto_lookup_row.set_enable_expansion(
                settings.auto_lookup_metadata
            )
            self._mb_row.set_active(settings.use_musicbrainz)
            self._freedb_row.set_active(settings.use_freedb)
            self._sync_lookup_source_sensitivity(settings.auto_lookup_metadata)
            self._rg_row.set_active(settings.apply_replaygain)
            self._embed_art_row.set_enable_expansion(settings.embed_artwork)
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
        self._rebuild_drive_rows(settings.device or self._device or '/dev/sr0')
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
        self._rebuild_drive_rows(text)

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

    def _on_rg_toggled(self, row: Adw.SwitchRow, *_args) -> None:
        self.store.update(apply_replaygain=row.get_active())

    def _on_ar_toggled(self, row: Adw.SwitchRow, *_args) -> None:
        self.store.update(verify_accuraterip=row.get_active())

    def _on_burst_toggled(self, row: Adw.SwitchRow, *_args) -> None:
        self.store.update(burst_fallback=row.get_active())

    def _on_log_toggled(self, row: Adw.SwitchRow, *_args) -> None:
        self.store.update(write_rip_log=row.get_active())

    def _on_auto_rip_toggled(self, row: Adw.SwitchRow, *_args) -> None:
        self.store.update(auto_rip=row.get_active())
        if row.get_active():
            self._toast('Auto-rip on — insert a disc to start')
        else:
            self._auto_rip_pending = False

    def _on_auto_eject_toggled(self, row: Adw.SwitchRow, *_args) -> None:
        self.store.update(auto_eject=row.get_active())

    def _on_embed_art_toggled(self, row: Adw.ExpanderRow, *_args) -> None:
        enabled = row.get_enable_expansion()
        self.store.update(embed_artwork=enabled)
        self._art_size_row.set_sensitive(enabled)
        if enabled and not row.get_expanded():
            row.set_expanded(True)

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

    @Gtk.Template.Callback()
    def _on_lookup_clicked(self, *_args) -> None:
        if self._ripping:
            return
        self._start_metadata_lookup(interactive=True)

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
            drive_c2_pointers=(
                settings.drive_c2_pointers if settings.drive_c2_configured else None
            ),
            drive_c2_message=settings.drive_c2_message,
            defeat_audio_cache=(
                settings.defeat_audio_cache or settings.drive_caches_audio
            ),
            rip_htoa=settings.rip_htoa,
            artwork_max_size=settings.artwork_max_size,
        )

        self._ripping = True
        self._rip_engine = RipEngine()
        self._set_ripping_ui(True)
        fmt = job.encode_format.upper()
        n = len(job.track_numbers)
        self.rip_title_label.set_label(f'Ripping {n} track{"s" if n != 1 else ""}')
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
        self.rip_revealer.set_reveal_child(True)

    def _hide_progress_panel(self, delay_ms: int = 0) -> None:
        self._cancel_progress_hide()
        if delay_ms <= 0:
            self.rip_revealer.set_reveal_child(False)
            return

        def _hide() -> bool:
            self._progress_hide_id = None
            if not self._ripping:
                self.rip_revealer.set_reveal_child(False)
            return GLib.SOURCE_REMOVE

        self._progress_hide_id = GLib.timeout_add(delay_ms, _hide)

    def _set_ripping_ui(self, active: bool) -> None:
        if active:
            self._show_progress_panel(title='Ripping')
        self.eject_button.set_sensitive(not active)
        self.lookup_button.set_sensitive(not active and self._disc is not None)
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
        # Keep a calm heading while work is in progress.
        if progress.state == RipState.REPLAYGAIN:
            self.rip_title_label.set_label('ReplayGain')
        elif progress.state == RipState.ENCODING:
            self.rip_title_label.set_label('Encoding')
        elif progress.state == RipState.VERIFYING:
            self.rip_title_label.set_label('Verifying')
        elif progress.state == RipState.PREPARING:
            self.rip_title_label.set_label('Preparing')
        elif progress.state == RipState.RIPPING:
            if progress.track_number > 0:
                self.rip_title_label.set_label(f'Track {progress.track_number}')
            else:
                self.rip_title_label.set_label('Ripping')
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
        if self._ripping:
            return
        device = self.store.get().device or self._device or '/dev/sr0'
        self._device = device
        status = query_drive_status(device)
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
            if need_load:
                self._refresh_disc(from_monitor=True)

    def _apply_drive_status_to_ui(self, status: DriveStatus) -> None:
        device = status.device or self._device or '/dev/sr0'
        self._rebuild_drive_rows(device)

    def _clear_disc_ui_for_empty(self, *, title: str, description: str) -> None:
        self._disc = None
        self._ids = None
        self._album = None
        self._clear_artwork()
        self.stack.set_visible_child_name('empty')
        self.rip_button.set_sensitive(False)
        self.lookup_button.set_sensitive(False)
        self.status_page.set_title(title)
        self.status_page.set_description(description)
        self._rebuild_track_list(None)
        self._fill_album_edit_fields(None)
        self._rebuild_disc_rows(None, None)
        self._rebuild_drive_rows(self.store.get().device or self._device or '/dev/sr0')
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
        settings = self.store.get()
        device = settings.device or self._device or '/dev/sr0'
        self._device = device

        status = query_drive_status(device)
        self._drive_status = status
        self._last_tray_state = status.state
        self._rebuild_drive_rows(self.store.get().device or self._device or '/dev/sr0')

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

        info = probe_disc(device)
        self._disc = info
        self._album = None
        self._ids = None
        self._clear_artwork()

        if info is None or not info.tracks:
            self.stack.set_visible_child_name('empty')
            self.rip_button.set_sensitive(False)
            self.lookup_button.set_sensitive(False)
            if status.state is DriveTrayState.NOT_READY:
                self.status_page.set_title('Drive not ready')
                self.status_page.set_description(
                    'Waiting for the drive to finish loading the disc…'
                )
            else:
                self.status_page.set_title('No audio CD detected')
                self.status_page.set_description(
                    f'A disc is present on {device}, but no audio tracks were found. '
                    'Insert an audio CD and press Refresh.'
                )
            self._album = None
            self._rebuild_track_list(None)
            self._fill_album_edit_fields(None)
            self._rebuild_disc_rows(None, None)
            self._rebuild_drive_rows(device)
            self._update_album_header(None, None)
            return

        self._ids = identifiers_from_disc(info)
        self.stack.set_visible_child_name('disc')
        self.rip_button.set_sensitive(True)
        self.lookup_button.set_sensitive(True)

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
        self._rebuild_drive_rows(device)
        self._fill_album_edit_fields(self._album)
        self._rebuild_track_list(info)
        self._update_album_header(info, self._album)

        if cached_art is not None:
            self._art_generation += 1
            self._artwork = cached_art
            self._prepare_embed_artwork()
            self._show_artwork(cached_art)
            base = self.metadata_source_label.get_label().split(' · art')[0]
            self.metadata_source_label.set_label(
                f'{base} · art: {cached_art.width}×{cached_art.height} '
                f'{cached_art.source} (cached)'
            )

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

    def _update_album_header(self, info: DiscInfo | None, album: AlbumMetadata | None) -> None:
        if album and (album.title or album.artist or album.medium_count > 1):
            self.album_title_label.set_label(album.title or 'Unknown Album')
            self.album_artist_label.set_label(album.artist or 'Unknown Artist')
            bits = [b for b in (album.date, album.label, album.country) if b]
            disc_n = max(1, int(album.medium_position or 1))
            disc_t = max(1, int(album.medium_count or 1))
            bits.insert(0, f'Disc {disc_n}/{disc_t}')
            self.album_meta_label.set_label(' · '.join(bits))
            source = album.source or 'unknown'
            extra = f' · {len(album.tracks)} tagged tracks' if album.tracks else ''
            self.metadata_source_label.set_label(f'Metadata: {source}{extra}')
            return

        self.album_title_label.set_label('Unknown Album')
        self.album_artist_label.set_label('Unknown Artist')
        if info is not None:
            total = sum(t.duration_seconds for t in info.tracks)
            minutes = int(total) // 60
            seconds = int(total) % 60
            self.album_meta_label.set_label(
                f'{info.track_count} tracks · {minutes}:{seconds:02d} total · {info.device}'
            )
        else:
            self.album_meta_label.set_label('')
        self.metadata_source_label.set_label(
            'Metadata: not looked up yet — press Lookup or enable auto-lookup'
        )
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
            self.disc_group.set_description('No disc inserted')
            row = Adw.ActionRow(
                title='Status',
                subtitle='Insert an audio CD and press Refresh',
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

    def _rebuild_drive_rows(self, device: str) -> None:
        for row in self._drive_rows:
            self.drive_group.remove(row)
        self._drive_rows.clear()

        settings = self.store.get()
        try:
            info: DriveInfo = probe_drive(device)
        except Exception as exc:  # noqa: BLE001
            info = DriveInfo(device=device, notes=[f'Probe failed: {exc}'])

        self.drive_group.set_description(info.display_name or device)

        st = self._drive_status or query_drive_status(device)
        self._drive_status = st
        tray_row = Adw.ActionRow(title='Tray / media', subtitle=st.label)
        if st.message and st.message != st.state.value:
            tray_row.set_tooltip_text(st.message)
        else:
            tray_row.set_tooltip_text(st.label)
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

        if settings.drive_c2_configured:
            c2_sub = 'Yes' if settings.drive_c2_pointers else 'No'
            if settings.drive_c2_message:
                c2_sub = f'{c2_sub} · {settings.drive_c2_message}'
        else:
            c2_sub = 'Not measured — run Drive setup'
        c2_row = Adw.ActionRow(title='C2 error pointers', subtitle=c2_sub)
        c2_row.set_tooltip_text(c2_sub)
        self.drive_group.add(c2_row)
        self._drive_rows.append(c2_row)

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
            self._rebuild_drive_rows(device)

        dialog.connect('closed', on_closed)
        dialog.present(self)

    def _build_album_edit_rows(self) -> None:
        """Create album-level EntryRows once (values refreshed on disc/lookup)."""
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
        self._album_artist_row.connect('changed', self._on_album_field_changed)
        self.album_edit_group.add(self._album_artist_row)
        self._album_field_rows.append(self._album_artist_row)

        self._album_date_row = Adw.EntryRow(title='Date')
        self._album_date_row.set_tooltip_text('Release date (e.g. 1997 or 1997-03-01)')
        self._album_date_row.connect('changed', self._on_album_field_changed)
        self.album_edit_group.add(self._album_date_row)
        self._album_field_rows.append(self._album_date_row)

        self._album_label_row = Adw.EntryRow(title='Label')
        self._album_label_row.connect('changed', self._on_album_field_changed)
        self.album_edit_group.add(self._album_label_row)
        self._album_field_rows.append(self._album_label_row)

        # Matches DISCNUMBER / TPOS style written into tags (e.g. 1/1, 2/3).
        self._album_disc_row = Adw.EntryRow(title='Disc')
        self._album_disc_row.set_text('1/1')
        self._album_disc_row.set_tooltip_text(
            'Disc position as disc/total (e.g. 1/1 or 2/3). Written as DISCNUMBER / TPOS.'
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
        self._suppress_meta_write = True
        try:
            if self._album_title_row is not None:
                self._album_title_row.set_text(album.title if album else '')
            if self._album_artist_row is not None:
                self._album_artist_row.set_text(album.artist if album else '')
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
        album = self._ensure_album_tracks()
        if self._album_title_row is not None:
            album.title = self._album_title_row.get_text().strip()
        if self._album_artist_row is not None:
            album.artist = self._album_artist_row.get_text().strip()
        if self._album_date_row is not None:
            album.date = self._album_date_row.get_text().strip()
        if self._album_label_row is not None:
            album.label = self._album_label_row.get_text().strip()
        if self._album_disc_row is not None:
            disc_num, disc_total = _parse_disc_field(
                self._album_disc_row.get_text()
            )
            album.medium_position = disc_num
            album.medium_count = disc_total
            # Normalize display to N/M while typing settles (only if already valid).
            normalized = f'{disc_num}/{disc_total}'
            current = self._album_disc_row.get_text().strip()
            if current and current != normalized and '/' in current:
                # Don't fight partial edits like "2/" while typing.
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
        self._track_title_rows.clear()
        self._track_artist_rows.clear()

        if info is None:
            self.track_edit_group.set_header_suffix(None)
            return

        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        btn_all = Gtk.Button(label='All')
        btn_all.add_css_class('flat')
        btn_all.connect('clicked', lambda *_: self._set_all_tracks(True))
        btn_none = Gtk.Button(label='None')
        btn_none.add_css_class('flat')
        btn_none.connect('clicked', lambda *_: self._set_all_tracks(False))
        header_box.append(btn_all)
        header_box.append(btn_none)
        self.track_edit_group.set_header_suffix(header_box)

        album = self._ensure_album_tracks()
        album_artist = album.artist or ''

        self._suppress_meta_write = True
        try:
            for track in info.tracks:
                meta = track_meta_for(album, track.number)
                title = meta.title if meta else ''
                artist = (meta.artist if meta else '') or album_artist

                expander = Adw.ExpanderRow(
                    title=f'{track.number:02d}. {title or "Track"}',
                    subtitle=f'{track.duration_label} · {track.length_sectors} sectors',
                )
                check = Gtk.CheckButton(active=True)
                check.set_valign(Gtk.Align.CENTER)
                expander.add_prefix(check)

                title_row = Adw.EntryRow(title='Title')
                title_row.set_text(title)
                title_row.connect(
                    'changed',
                    lambda row, n=track.number: self._on_track_field_changed(n),
                )
                expander.add_row(title_row)

                artist_row = Adw.EntryRow(title='Artist')
                artist_row.set_text(artist)
                artist_row.connect(
                    'changed',
                    lambda row, n=track.number: self._on_track_field_changed(n),
                )
                expander.add_row(artist_row)

                self.track_edit_group.add(expander)
                self._track_rows.append(expander)
                self._track_checks[track.number] = check
                self._track_title_rows[track.number] = title_row
                self._track_artist_rows[track.number] = artist_row
        finally:
            self._suppress_meta_write = False

        self.track_edit_group.set_title(
            f'Track metadata ({info.track_count}) — select and edit'
        )

    def _on_track_field_changed(self, track_number: int) -> None:
        if self._suppress_meta_write or self._disc is None:
            return
        album = self._ensure_album_tracks()
        title_row = self._track_title_rows.get(track_number)
        artist_row = self._track_artist_rows.get(track_number)
        title = title_row.get_text().strip() if title_row else ''
        artist = artist_row.get_text().strip() if artist_row else ''

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

        # Refresh expander title without rebuilding (preserves focus).
        for row in self._track_rows:
            if not isinstance(row, Adw.ExpanderRow):
                continue
            # Match by track number prefix
            current = row.get_title() or ''
            if current.startswith(f'{track_number:02d}.'):
                row.set_title(f'{track_number:02d}. {title or "Track"}')
                break

        _mark_album_edited(album)
        self._schedule_cache_save()

    def _set_all_tracks(self, active: bool) -> None:
        for check in self._track_checks.values():
            check.set_active(active)

    def _apply_ar_results_to_rows(self, results) -> None:
        by_num = {r.track_number: r for r in results}
        for num, ar in by_num.items():
            for row in self._track_rows:
                if not isinstance(row, Adw.ExpanderRow):
                    continue
                title = row.get_title() or ''
                if title.startswith(f'{num:02d}.'):
                    sub = row.get_subtitle() or ''
                    # Avoid stacking AR messages on repeated rebuilds
                    base = sub.split(' · AR ')[0].split(' · Accurately')[0]
                    row.set_subtitle(f'{base} · {ar.message}')
                    break

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
        self.lookup_button.set_sensitive(False)
        self.lookup_progress.set_visible(True)
        self.lookup_progress.pulse()
        self.metadata_source_label.set_label('Metadata: looking up…')

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
        self.lookup_button.set_sensitive(self._disc is not None)

        if error:
            self.metadata_source_label.set_label(f'Metadata: error — {error}')
            if interactive:
                self._toast(f'Lookup failed: {error}')
            return

        if not results:
            self.metadata_source_label.set_label('Metadata: no matches found')
            if interactive:
                self._toast('No metadata matches for this disc')
            return

        if len(results) == 1 and not interactive:
            self._apply_album(results[0])
            self._toast(f'Metadata: {results[0].display_label}')
            return

        if len(results) == 1:
            self._apply_album(results[0])
            self._toast(f'Using {results[0].source}: {results[0].title}')
            return

        # Multiple matches — always let the user pick when interactive,
        # and also when auto-lookup finds several candidates.
        self._show_picker(results)

    def _show_picker(self, candidates: list[AlbumMetadata]) -> None:
        dialog = MetadataPickerDialog(candidates)

        def on_closed(*_a) -> None:
            chosen = dialog.chosen
            if chosen is not None:
                self._apply_album(chosen)
                self._toast(f'Using {chosen.source}: {chosen.title}')
            elif self._album is None:
                self.metadata_source_label.set_label(
                    f'Metadata: {len(candidates)} matches (none selected)'
                )

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
        if self.store.get().fetch_artwork:
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
        base = self.metadata_source_label.get_label().split(' · art')[0]
        self.metadata_source_label.set_label(base)
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
            base = self.metadata_source_label.get_label().split(' · art')[0]
            emb = self._artwork_embed
            embed_note = (
                f', embed {emb.width}×{emb.height}' if emb is not None else ''
            )
            self.metadata_source_label.set_label(
                f'{base} · art: {image.width}×{image.height} local{embed_note}'
            )
            self._toast(f'Local cover art {image.label}')
            self._schedule_cache_save()

        dialog.open(self, None, on_done)

    def _clear_artwork(self) -> None:
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
        self.metadata_source_label.set_label(
            self.metadata_source_label.get_label().split(' · art')[0]
            + ' · art: downloading…'
        )

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

        base_meta = self.metadata_source_label.get_label().split(' · art')[0]

        if error:
            self.metadata_source_label.set_label(f'{base_meta} · art: error')
            self._toast(f'Artwork failed: {error}')
            return

        if image is None:
            self.metadata_source_label.set_label(f'{base_meta} · art: none found')
            return

        self._artwork = image
        self._prepare_embed_artwork()
        self._show_artwork(image)

        emb = self._artwork_embed
        embed_note = ''
        if emb is not None:
            embed_note = f', embed {emb.width}×{emb.height}'
        self.metadata_source_label.set_label(
            f'{base_meta} · art: {image.width}×{image.height} {image.source}{embed_note}'
        )
        self._toast(f'Cover art {image.label}')
        self._schedule_cache_save()

    def _prepare_embed_artwork(self) -> None:
        if self._artwork is None:
            self._artwork_embed = None
            return
        max_edge = self.store.get().artwork_max_size
        self._artwork_embed = ArtworkFetcher().resize(self._artwork, max_edge)

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
            return

        # Hide the dimmed music glyph once real art is loaded.
        self.cover_placeholder.set_visible(False)
        self.cover_picture.set_size_request(size, size)
        self.cover_overlay.set_size_request(size, size)
        self.cover_frame.set_size_request(size, size)
        self._sync_cover_action_sensitivity()

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

    @property
    def artwork_full(self) -> ArtworkImage | None:
        """Full-resolution downloaded cover (for folder.jpg later)."""
        return self._artwork

    @property
    def artwork_for_embed(self) -> ArtworkImage | None:
        """Cover scaled to the user-selected embed size."""
        return self._artwork_embed


def _parse_disc_field(text: str) -> tuple[int, int]:
    """Parse disc field as N/M (e.g. 1/1) or a single N. Defaults to 1/1."""
    raw = (text or '').strip()
    if not raw:
        return 1, 1
    if '/' in raw:
        left, _, right = raw.partition('/')
        try:
            disc = max(1, int(left.strip() or '1'))
        except ValueError:
            disc = 1
        try:
            total = max(1, int(right.strip() or '1'))
        except ValueError:
            total = max(1, disc)
        if disc > total:
            total = disc
        return disc, total
    try:
        disc = max(1, int(raw))
    except ValueError:
        return 1, 1
    return disc, disc


def _mark_album_edited(album: AlbumMetadata) -> None:
    """Annotate metadata source after a manual edit."""
    source = album.source or ''
    if source in ('musicbrainz', 'freedb') and not source.endswith('+manual'):
        album.source = f'{source}+manual'
    elif not source:
        album.source = 'manual'
