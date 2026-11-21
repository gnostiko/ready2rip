# SPDX-License-Identifier: GPL-3.0-or-later
"""Scratch directories for dual-pass WAVs (prefer output/cache over /tmp)."""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

log = logging.getLogger(__name__)


def _try_mkdir(path: Path) -> Path | None:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / '.ready2rip-write-test'
        probe.write_bytes(b'')
        probe.unlink(missing_ok=True)
        return path
    except OSError as exc:
        log.debug('Scratch dir unusable %s: %s', path, exc)
        return None


def preferred_scratch_parent(output_directory: Path | None) -> Path:
    """Best parent for rip scratch (output album root, then XDG cache)."""
    if output_directory is not None:
        out = Path(output_directory).expanduser()
        candidate = _try_mkdir(out / '.ready2rip-tmp')
        if candidate is not None:
            return candidate
        parent = _try_mkdir(out)
        if parent is not None:
            nested = _try_mkdir(parent / '.ready2rip-tmp')
            if nested is not None:
                return nested
    xdg = os.environ.get('XDG_CACHE_HOME', '').strip()
    cache_root = Path(xdg) if xdg else Path.home() / '.cache'
    cached = _try_mkdir(cache_root / 'ready2rip' / 'tmp')
    if cached is not None:
        return cached
    return Path(tempfile.gettempdir())


@contextmanager
def rip_scratch_dir(output_directory: Path | None = None) -> Iterator[Path]:
    """Temporary directory for extract/encode intermediates; cleaned on exit."""
    parent = preferred_scratch_parent(output_directory)
    tmp = Path(tempfile.mkdtemp(prefix='ready2rip-', dir=str(parent)))
    try:
        yield tmp
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        # Drop empty .ready2rip-tmp under the output tree.
        try:
            if parent.name == '.ready2rip-tmp' and parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
        except OSError:
            pass
