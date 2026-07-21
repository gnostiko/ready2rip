# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared validation and safety helpers for ready2rip."""

from __future__ import annotations

import re
import shutil
import urllib.parse
from pathlib import Path

# Optical devices commonly used on Linux (plus by-id / by-path links under /dev).
_DEVICE_OK = re.compile(
    r'^/dev/'
    r'(?:'
    r'sr\d+|sg\d+|scd\d+|'
    r'cdrom\d*|cdrw\d*|dvd\d*|dvdrw\d*|'
    r'cd\d*|cdwriter\d*|'
    r'disk/by-(?:id|path|uuid)/[^/\x00]+'
    r')$'
)

# Characters that must never appear in a device path passed to external tools.
_DEVICE_BAD = re.compile(r'[\x00-\x1f;&|`$<>(){}\n\r]')

# Max remote image payload we will hold in memory (bytes).
MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024


def find_cdparanoia() -> str | None:
    """Return path to ``cdparanoia`` or libcdio's ``cd-paranoia`` binary."""
    return shutil.which('cdparanoia') or shutil.which('cd-paranoia')


def validate_device_path(device: str | None, *, default: str = '/dev/sr0') -> str:
    """Return a safe optical device path for use with cdparanoia / eject.

    Rejects shell metacharacters, path traversal, and non-``/dev`` locations.
    """
    raw = (device or '').strip() or default
    if _DEVICE_BAD.search(raw):
        raise ValueError(f'Invalid optical device path: {raw!r}')
    parts = [p for p in raw.split('/') if p]
    if not parts or parts[0] != 'dev':
        raise ValueError('Optical device must be a path under /dev')
    if '..' in parts or '.' in parts:
        raise ValueError(f'Invalid optical device path: {raw!r}')
    normalized = '/' + '/'.join(parts)
    if _DEVICE_OK.match(normalized):
        return normalized
    # Allow simple single-name nodes such as /dev/sr0 if regex drifts.
    if re.fullmatch(r'/dev/[A-Za-z][A-Za-z0-9._+-]*', normalized):
        return normalized
    raise ValueError(f'Unsupported optical device path: {normalized!r}')


def is_safe_http_url(url: str) -> bool:
    """True if *url* is http(s) without credentials (safe to fetch for art/meta)."""
    if not url or not isinstance(url, str):
        return False
    try:
        parsed = urllib.parse.urlparse(url.strip())
    except ValueError:
        return False
    if parsed.scheme not in ('http', 'https'):
        return False
    if not parsed.netloc or parsed.netloc.startswith('.'):
        return False
    if parsed.username is not None or parsed.password is not None:
        return False
    host = (parsed.hostname or '').casefold()
    if host in {'localhost', '127.0.0.1', '::1', '0.0.0.0'}:
        return False
    return True


def ensure_path_under(base: Path, path: Path) -> Path:
    """Ensure *path* resolves inside *base*; raise ``ValueError`` if not."""
    base_resolved = base.expanduser().resolve()
    path_exp = path.expanduser()
    try:
        candidate = path_exp.resolve(strict=False)
    except TypeError:
        candidate = path_exp.resolve()
    try:
        candidate.relative_to(base_resolved)
    except ValueError as exc:
        raise ValueError(
            f'Path {path} escapes output directory {base_resolved}'
        ) from exc
    return candidate


def read_limited(response, max_bytes: int = MAX_DOWNLOAD_BYTES) -> bytes:
    """Read an HTTP response body with a hard size cap (DoS / memory guard)."""
    chunks: list[bytes] = []
    total = 0
    try:
        cl = response.headers.get('Content-Length')
        if cl is not None and int(cl) > max_bytes:
            raise ValueError(f'Response too large ({cl} bytes)')
    except (TypeError, ValueError) as exc:
        if 'too large' in str(exc):
            raise
    while True:
        block = response.read(64 * 1024)
        if not block:
            break
        total += len(block)
        if total > max_bytes:
            raise ValueError(f'Response exceeded {max_bytes} bytes')
        chunks.append(block)
    return b''.join(chunks)
