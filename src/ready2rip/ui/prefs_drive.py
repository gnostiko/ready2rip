# SPDX-License-Identifier: GPL-3.0-or-later
"""Drive preference rows for the main window."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import gi

gi.require_version('Adw', '1')
from gi.repository import Adw

if TYPE_CHECKING:
    from ready2rip.window import Ready2RipWindow


def build_drive_settings(window: Ready2RipWindow, settings: Any) -> None:
    """Populate device group: path, calibration, overread status."""
    window._device_row = Adw.EntryRow(title='Device')
    window._device_row.set_text(settings.device or '/dev/sr0')
    window._device_row.set_tooltip_text('Optical drive path (for example /dev/sr0)')
    window._device_row.connect('changed', window._on_device_path_changed)
    window._add_pref(window.device_group, window._device_row)
    window._build_calibration_row()
    window._overread_row = Adw.ActionRow(
        title='Overread',
        subtitle='Unknown until a rip probes the drive',
    )
    window._overread_row.set_tooltip_text(
        'Lead-in/out overread capability. When No, offset edges are '
        'silence-filled and last-track AccurateRip can be unreliable.'
    )
    window._add_pref(window.device_group, window._overread_row)
