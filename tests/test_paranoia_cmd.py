# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for paranoia argv builder and backend detection."""

from __future__ import annotations

from ready2rip.disc.overread import OverreadCapability
from ready2rip.rip.paranoia_cmd import (
    absolute_span_arg,
    build_paranoia_argv,
    want_force_overread,
)
from ready2rip.util import paranoia_backend


def test_paranoia_backend_names() -> None:
    assert paranoia_backend('/usr/bin/cd-paranoia') == 'libcdio'
    assert paranoia_backend('/usr/bin/cdparanoia') == 'xiph'
    assert paranoia_backend(None) == 'unknown'
    assert paranoia_backend('/opt/bin/foo') == 'unknown'


def test_absolute_span_arg_relative_end() -> None:
    assert absolute_span_arg(100, 150) == '[.100]-[.49]'


def test_want_force_overread_edges() -> None:
    both = OverreadCapability(lead_in=True, lead_out=True)
    lead_out = OverreadCapability(lead_in=False, lead_out=True)
    lead_in = OverreadCapability(lead_in=True, lead_out=False)
    assert want_force_overread(667, both) is True
    assert want_force_overread(667, lead_out) is True
    assert want_force_overread(667, lead_in) is False
    assert want_force_overread(-48, lead_in) is True
    assert want_force_overread(-48, lead_out) is False
    assert want_force_overread(0, both) is False
    assert want_force_overread(6, None) is False


def test_build_paranoia_argv_force_overread_libcdio_only() -> None:
    lib = build_paranoia_argv(
        '/usr/bin/cd-paranoia',
        '/dev/sr0',
        mode='secure',
        sample_offset=667,
        force_overread=True,
    )
    assert '--force-overread' in lib
    assert '-O' in lib
    assert '667' in lib

    xiph = build_paranoia_argv(
        '/usr/bin/cdparanoia',
        '/dev/sr0',
        mode='secure',
        sample_offset=667,
        force_overread=True,
    )
    assert '--force-overread' not in xiph


def test_build_paranoia_argv_burst_quiet() -> None:
    cmd = build_paranoia_argv(
        '/usr/bin/cd-paranoia',
        '/dev/sr0',
        mode='burst',
        quiet=True,
    )
    assert cmd[0] == '/usr/bin/cd-paranoia'
    assert '-Z' in cmd
    assert '-q' in cmd
