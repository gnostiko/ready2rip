# SPDX-License-Identifier: GPL-3.0-or-later
"""SettingsStore snapshot cache behaviour (requires PyGObject)."""

from __future__ import annotations

import pytest

gi = pytest.importorskip('gi')
gi.require_version('Gio', '2.0')
gi.require_version('GLib', '2.0')

from ready2rip.settings import SettingsStore


def test_settings_store_update_visible_on_next_get() -> None:
    store = SettingsStore()
    before = store.get().auto_eject
    store.update(auto_eject=not before)
    assert store.get().auto_eject is (not before)


def test_settings_store_get_returns_independent_copies() -> None:
    store = SettingsStore()
    first = store.get()
    original_device = first.device
    first.device = '/dev/mutated-by-caller'
    assert store.get().device == original_device


def test_settings_store_repeated_get_matches() -> None:
    store = SettingsStore()
    a = store.get()
    b = store.get()
    assert a == b
