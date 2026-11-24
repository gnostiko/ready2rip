# SPDX-License-Identifier: GPL-3.0-or-later
"""HTOA post-encode title retag covers every encode suffix."""

from __future__ import annotations

import inspect

from ready2rip.rip.engine_helpers import _retag_title
from ready2rip.rip.plan import ENCODE_EXTENSIONS


def test_retag_title_covers_all_encode_suffixes() -> None:
    src = inspect.getsource(_retag_title)
    for suffix in sorted(ENCODE_EXTENSIONS.values()):
        assert repr(suffix) in src or f"'{suffix}'" in src, suffix
    # AIFF alternate extension used by mutagen paths.
    assert "'.aif'" in src
