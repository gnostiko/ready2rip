# SPDX-License-Identifier: GPL-3.0-or-later
"""Import namespace for Ready2RipWindow mixin modules."""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gio, GLib, GObject, Gtk, Pango

from ready2rip.accuraterip import AccurateRipConfidence
from ready2rip.artwork.fetch import (
    ArtworkFetcher,
    ArtworkImage,
    ArtworkSourceOptions,
    apply_artwork_to_picture,
)
from ready2rip.ctdb import CtdbConfidence
from ready2rip.disc.cdmeta import enrich_album_from_disc
from ready2rip.disc.discid_util import DiscIdentifiers, identifiers_from_disc
from ready2rip.disc.drive_info import DriveInfo, probe_drive
from ready2rip.disc.drive_status import (
    DriveStatus,
    DriveTrayState,
    eject_drive,
    empty_tray_copy,
    query_drive_status,
)
from ready2rip.disc.probe import DiscInfo, probe_disc
from ready2rip.drive_setup import needs_drive_setup
from ready2rip.icons import apply_icon
from ready2rip.metadata.album_edit import gap_fill_album
from ready2rip.metadata.models import AlbumMetadata
from ready2rip.metadata.providers import (
    fetch_album_from_musicbrainz_link,
    lookup_metadata,
    parse_musicbrainz_release_id,
)
from ready2rip.metadata_dialog import MetadataPickerDialog
from ready2rip.paths import track_meta_for
from ready2rip.rip.engine import RipEngine, RipJob, RipProgress, RipResult, RipState
from ready2rip.settings import (
    SettingsStore,
    normalize_also_encode_folder,
    normalize_also_encode_format,
    normalize_encode_format,
    normalize_existing_files_policy,
    normalize_lyrics_save_mode,
    normalize_qaac_quality,
    normalize_wavpack_level,
)
from ready2rip.setup_dialog import DriveSetupDialog
from ready2rip.util import validate_device_path
from ready2rip.window_styles import banner_fill_css

log = logging.getLogger(__name__)
