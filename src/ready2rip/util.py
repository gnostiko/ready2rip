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
    return host not in {'localhost', '127.0.0.1', '::1', '0.0.0.0'}


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


def parse_disc_field(text: str) -> tuple[int, int]:
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


def normalize_release_date(*candidates: str) -> str:
    """Return the most complete release date among *candidates*.

    Accepts ``YYYY``, ``YYYY-MM``, or ``YYYY-MM-DD`` (slashes allowed). Month and
    day are zero-padded when present; missing parts are omitted (never filled
    with ``00``). Prefers the candidate with the most precision.
    """
    best = ''
    best_parts = -1
    for raw in candidates:
        parsed = _parse_partial_iso_date(raw)
        if not parsed:
            continue
        parts = parsed.count('-') + 1
        if parts > best_parts:
            best = parsed
            best_parts = parts
    return best


def _parse_partial_iso_date(raw: str) -> str:
    text = (raw or '').strip()
    if not text:
        return ''
    text = text.replace('/', '-').split('T', 1)[0].split(' ', 1)[0].strip()
    match = re.match(
        r'^(\d{4})(?:-(\d{1,2})(?:-(\d{1,2}))?)?$',
        text,
    )
    if not match:
        return ''
    year = match.group(1)
    month = match.group(2)
    day = match.group(3)
    if month is None:
        return year
    month_i = int(month)
    if month_i < 1 or month_i > 12:
        return year
    if day is None:
        return f'{year}-{month_i:02d}'
    day_i = int(day)
    if day_i < 1 or day_i > 31:
        return f'{year}-{month_i:02d}'
    return f'{year}-{month_i:02d}-{day_i:02d}'


