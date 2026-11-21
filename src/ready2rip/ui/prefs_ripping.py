# SPDX-License-Identifier: GPL-3.0-or-later
"""Ripping / verify preference rows for the main window."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import gi

gi.require_version('Adw', '1')
gi.require_version('Gtk', '4.0')
from gi.repository import Adw, Gtk

from ready2rip.settings_defs import RIP_MODES, normalize_rip_mode

if TYPE_CHECKING:
    from ready2rip.window import Ready2RipWindow


def build_ripping_settings(window: Ready2RipWindow, settings: Any) -> None:
    """Populate verify / extras / automation groups on *window*."""
    window._rip_modes = [mid for mid, _label in RIP_MODES]
    window._rip_mode_row = Adw.ComboRow(title='Rip mode')
    window._rip_mode_row.set_model(
        Gtk.StringList.new([label for _mid, label in RIP_MODES])
    )
    window._rip_mode_row.set_tooltip_text(
        'Archival: secure + test & copy. '
        'Normal: paranoia with repair allowed. '
        'Burst: fast -Z (not archival-grade).'
    )
    window._select_combo(
        window._rip_mode_row,
        window._rip_modes,
        normalize_rip_mode(settings.rip_mode),
    )
    window._rip_mode_row.connect('notify::selected', window._on_rip_mode_changed)
    window._add_pref(window.verify_group, window._rip_mode_row)

    switches: list[tuple[str, Adw.PreferencesGroup, str, str, bool, object]] = [
        (
            '_ar_row',
            window.verify_group,
            'AccurateRip',
            'Verify tracks against the AccurateRip database',
            settings.verify_accuraterip,
            window._on_ar_toggled,
        ),
        (
            '_require_db_match_row',
            window.verify_group,
            'Require AccurateRip match',
            'Fail archival unless at least one track MATCHES AccurateRip',
            settings.require_db_match,
            window._on_require_db_match_toggled,
        ),
        (
            '_archival_strict_row',
            window.verify_group,
            'Strict archival',
            'Fail archival on AccurateRip/CTDB mismatches; off = Secure OK with partial DB notes',
            settings.archival_strict,
            window._on_archival_strict_toggled,
        ),
        (
            '_ctdb_row',
            window.verify_group,
            'CTDB',
            'Verify tracks against the CUETools Database',
            settings.verify_ctdb,
            window._on_ctdb_toggled,
        ),
        (
            '_submit_ctdb_row',
            window.verify_group,
            'Submit to CTDB',
            'Confirm matches or submit new parity from secure, high-quality rips',
            settings.submit_ctdb,
            window._on_submit_ctdb_toggled,
        ),
        (
            '_burst_row',
            window.verify_group,
            'Burst fallback',
            'Allow one insecure -Z recovery after Archival retries fail',
            settings.burst_fallback,
            window._on_burst_toggled,
        ),
        (
            '_copy_image_row',
            window.extras_group,
            'Copy image',
            'Write one continuous disc image instead of per-track files',
            settings.copy_image,
            window._on_copy_image_toggled,
        ),
        (
            '_htoa_row',
            window.extras_group,
            'Hidden track (HTOA)',
            'Save non-silent audio before track 1 as track 00',
            settings.rip_htoa,
            window._on_htoa_toggled,
        ),
        (
            '_data_tracks_row',
            window.extras_group,
            'Data tracks',
            'Rip mixed-mode / CD-Extra data as raw .bin (lossless rips only)',
            settings.rip_data_tracks,
            window._on_data_tracks_toggled,
        ),
        (
            '_log_row',
            window.extras_group,
            'Write log',
            'Save a detailed rip log in the album folder',
            settings.write_rip_log,
            window._on_log_toggled,
        ),
        (
            '_cue_row',
            window.extras_group,
            'Write CUE sheet',
            'Write a .cue sheet next to the ripped files',
            settings.write_cue_file,
            window._on_cue_toggled,
        ),
        (
            '_auto_rip_row',
            window.automation_group,
            'Auto-rip',
            'Start ripping when an audio CD is detected',
            settings.auto_rip,
            window._on_auto_rip_toggled,
        ),
        (
            '_auto_eject_row',
            window.automation_group,
            'Auto-eject',
            'Eject the disc when a rip finishes',
            settings.auto_eject,
            window._on_auto_eject_toggled,
        ),
    ]
    for attr, group, title, tooltip, active, handler in switches:
        row = window._make_switch_row(
            title=title, tooltip=tooltip, active=active, handler=handler
        )
        setattr(window, attr, row)
        window._add_pref(group, row)
    window._sync_rip_mode_sensitivity(normalize_rip_mode(settings.rip_mode))
    window._sync_require_db_match_sensitivity(bool(settings.verify_accuraterip))
