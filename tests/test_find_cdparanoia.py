# SPDX-License-Identifier: GPL-3.0-or-later
"""find_cdparanoia prefers libcdio's cd-paranoia."""

from __future__ import annotations

from ready2rip import util


def test_find_cdparanoia_prefers_libcdio(monkeypatch) -> None:
    def fake_which(name: str) -> str | None:
        if name == 'cd-paranoia':
            return '/usr/bin/cd-paranoia'
        if name == 'cdparanoia':
            return '/usr/bin/cdparanoia'
        return None

    monkeypatch.setattr(util.shutil, 'which', fake_which)
    assert util.find_cdparanoia() == '/usr/bin/cd-paranoia'


def test_find_cdparanoia_falls_back_to_xiph(monkeypatch) -> None:
    def fake_which(name: str) -> str | None:
        if name == 'cdparanoia':
            return '/usr/bin/cdparanoia'
        return None

    monkeypatch.setattr(util.shutil, 'which', fake_which)
    assert util.find_cdparanoia() == '/usr/bin/cdparanoia'
