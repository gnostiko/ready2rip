# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared validation and safety helpers for ready2rip."""

from __future__ import annotations

import ipaddress
import os
import re
import shutil
import socket
import urllib.parse
from collections.abc import Mapping, Sequence
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

# Max artwork payload we will hold in memory (bytes).
# Applies to local picks and remote downloads (CAA / iTunes / cover URL).
MAX_DOWNLOAD_BYTES = 100 * 1024 * 1024

# Cap for draining HTTPError bodies (ResourceWarning avoidance only).
_HTTP_ERROR_DRAIN_BYTES = 64 * 1024

# Host suffixes we intentionally fetch (metadata / art / AccurateRip / CTDB).
# Blocks DNS-rebinding SSRF to arbitrary public hosts.
_FETCH_HOST_SUFFIXES = (
    'musicbrainz.org',
    'coverartarchive.org',
    'archive.org',  # CAA CDN redirects
    'itunes.apple.com',
    'mzstatic.com',  # iTunes artwork CDN
    'accuraterip.com',
    'gnudb.org',
    'gnudb.gnudb.org',
    'cuetools.net',  # CTDB lookup + parity CDN
    'cue.tools',  # CTDB HTTPS frontend
    'lrclib.net',  # lyrics
)


def find_cdparanoia() -> str | None:
    """Return path to a paranoia binary.

    Prefers libcdio-paranoia (``cd-paranoia``) when both are installed; falls
    back to classic Xiph ``cdparanoia``.
    """
    return shutil.which('cd-paranoia') or shutil.which('cdparanoia')


def paranoia_backend(path: str | None) -> str:
    """Classify a paranoia binary: ``libcdio``, ``xiph``, or ``unknown``."""
    if not path:
        return 'unknown'
    try:
        name = Path(path).resolve().name
    except OSError:
        name = Path(path).name
    lowered = name.lower()
    if lowered in ('cd-paranoia', 'cd-paranoia.exe'):
        return 'libcdio'
    if lowered in ('cdparanoia', 'cdparanoia.exe'):
        # AppImage may ship libcdio as ``cdparanoia`` via symlink.
        try:
            resolved = Path(path).resolve().name.lower()
        except OSError:
            resolved = lowered
        if resolved in ('cd-paranoia', 'cd-paranoia.exe'):
            return 'libcdio'
        return 'xiph'
    if 'cd-paranoia' in lowered:
        return 'libcdio'
    if 'cdparanoia' in lowered:
        return 'xiph'
    return 'unknown'


# ---------------------------------------------------------------------------
# AppImage / host subprocess environment
# ---------------------------------------------------------------------------
# AppRun exports LD_LIBRARY_PATH (and friends) for the bundled GTK/Python stack.
# Host tools (Wine/qaac wrappers, udevadm, cdrdao, …) must not inherit that
# view — Ubuntu 22.04 libreadline under Solus bash is the canonical failure:
# ``undefined symbol: rl_trim_arg_from_keyseq``.

_APPDIR_PATH_ENV_KEYS = (
    'LD_LIBRARY_PATH',
    'GI_TYPELIB_PATH',
    'PYTHONPATH',
    'GSETTINGS_SCHEMA_DIR',
    'GDK_PIXBUF_MODULE_FILE',
    'SSL_CERT_DIR',
)


def appdir_root() -> Path | None:
    """Resolved ``$APPDIR`` when running inside an AppImage / AppDir, else None."""
    raw = (os.environ.get('APPDIR') or '').strip()
    if not raw:
        return None
    try:
        return Path(raw).resolve()
    except OSError:
        return Path(raw)


def path_is_under_appdir(path: str | Path, appdir: Path | None = None) -> bool:
    """True if *path* resolves inside the active AppDir."""
    root = appdir if appdir is not None else appdir_root()
    if root is None:
        return False
    try:
        resolved = Path(path).expanduser().resolve()
    except OSError:
        return False
    try:
        resolved.relative_to(root)
        return True
    except ValueError:
        return False


def argv_primary_executable(argv: Sequence[str]) -> str | None:
    """Best-effort path/name of the real binary in *argv* (skips ``env``/``stdbuf``)."""
    if not argv:
        return None
    args = [str(a) for a in argv]
    i = 0
    head = Path(args[0]).name
    if head == 'env':
        i = 1
        while i < len(args):
            tok = args[i]
            if tok == '--':
                i += 1
                break
            if tok.startswith('-'):
                i += 1
                continue
            if '=' in tok:
                i += 1
                continue
            break
    if i >= len(args):
        return None
    if Path(args[i]).name == 'stdbuf':
        i += 1
        while i < len(args) and args[i].startswith('-'):
            opt = args[i]
            if opt in ('-o', '-e', '-i') and i + 1 < len(args):
                i += 2
            else:
                i += 1
    if i >= len(args):
        return None
    return args[i]


def resolve_argv_executable(argv: Sequence[str]) -> Path | None:
    """Resolve :func:`argv_primary_executable` to an absolute path when possible."""
    raw = argv_primary_executable(argv)
    if not raw:
        return None
    path = Path(raw).expanduser()
    if path.is_file():
        try:
            return path.resolve()
        except OSError:
            return path
    found = shutil.which(raw)
    if found:
        try:
            return Path(found).resolve()
        except OSError:
            return Path(found)
    return None


def _filter_path_env(value: str, appdir: Path) -> str | None:
    """Drop AppDir entries from a ``PATH``-style env value; None if empty."""
    kept: list[str] = []
    for part in value.split(':'):
        if not part:
            continue
        try:
            resolved = Path(part).resolve()
        except OSError:
            kept.append(part)
            continue
        try:
            resolved.relative_to(appdir)
            continue
        except ValueError:
            kept.append(part)
    if not kept:
        return None
    return ':'.join(kept)


def subprocess_env_for(
    argv: Sequence[str],
    *,
    base: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Env for ``subprocess``: scrub AppDir linker paths for host binaries.

    Bundled AppDir tools keep the full AppRun environment. Host tools (Wine,
    qaac wrappers, udevadm, …) get AppDir removed from ``LD_LIBRARY_PATH`` and
    related variables so they load the host library stack.
    """
    env = dict(os.environ if base is None else base)
    appdir = appdir_root()
    if appdir is None:
        return env

    exe = resolve_argv_executable(argv)
    if exe is not None and path_is_under_appdir(exe, appdir):
        return env

    # Host (or unresolved) binary: strip AppDir from the linker / GI view.
    for key in _APPDIR_PATH_ENV_KEYS:
        raw = env.get(key)
        if not raw:
            continue
        if key in {'GSETTINGS_SCHEMA_DIR', 'GDK_PIXBUF_MODULE_FILE', 'SSL_CERT_DIR'}:
            if path_is_under_appdir(raw, appdir):
                env.pop(key, None)
            continue
        filtered = _filter_path_env(raw, appdir)
        if filtered is None:
            env.pop(key, None)
        else:
            env[key] = filtered

    python_home = env.get('PYTHONHOME')
    if python_home and path_is_under_appdir(python_home, appdir):
        env.pop('PYTHONHOME', None)

    ssl_cert = env.get('SSL_CERT_FILE')
    if ssl_cert and path_is_under_appdir(ssl_cert, appdir):
        # Host OpenSSL/Wine should use the system trust store.
        env.pop('SSL_CERT_FILE', None)
        env.pop('CURL_CA_BUNDLE', None)
        env.pop('REQUESTS_CA_BUNDLE', None)

    return env


def validate_device_path(device: str | None, *, default: str = '/dev/sr0') -> str:
    """Return a safe optical device path for use with cdparanoia / eject.

    Rejects shell metacharacters, path traversal, non-``/dev`` locations, and
    non-optical node names (no fallback to ``/dev/sda``, ``/dev/mem``, etc.).
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
    raise ValueError(f'Unsupported optical device path: {normalized!r}')


def is_safe_http_url(url: str) -> bool:
    """True if *url* is an allowlisted http(s) host resolving to a public IP.

    Requires a known metadata/art/AccurateRip host suffix **and** public
    resolved addresses. That blocks private/link-local SSRF and DNS rebinding
    to arbitrary public sites. Redirects must pass the same check.
    """
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
    host = parsed.hostname
    if not host:
        return False
    host_cf = host.casefold()
    if host_cf in {'localhost', '127.0.0.1', '::1', '0.0.0.0', '[::1]'}:
        return False
    if host_cf.endswith('.localhost') or host_cf.endswith('.local'):
        return False
    if not _is_allowed_fetch_host(host_cf):
        return False

    # Literal IP hosts are never on the suffix allowlist — reject.
    try:
        ipaddress.ip_address(host)
        return False
    except ValueError:
        pass

    # Numeric IPv4 spellings urllib leaves as hostnames (e.g. 2130706433).
    if re.fullmatch(r'\d{1,10}', host) or re.fullmatch(
        r'\d{1,3}(?:\.\d{1,3}){1,3}', host
    ):
        return False

    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return False
    if not infos:
        return False
    for info in infos:
        addr = info[4][0]
        try:
            if not _is_public_ip(ipaddress.ip_address(addr)):
                return False
        except ValueError:
            return False
    return True


def _is_allowed_fetch_host(host: str) -> bool:
    return any(
        host == suffix or host.endswith('.' + suffix)
        for suffix in _FETCH_HOST_SUFFIXES
    )


def _is_public_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast:
        return False
    if ip.is_reserved or ip.is_unspecified:
        return False
    # CGNAT / documentation / benchmarking ranges.
    if isinstance(ip, ipaddress.IPv4Address):
        if ip in ipaddress.ip_network('100.64.0.0/10'):
            return False
        if ip in ipaddress.ip_network('198.18.0.0/15'):
            return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return _is_public_ip(ip.ipv4_mapped)
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


def drain_http_error(exc, max_bytes: int = _HTTP_ERROR_DRAIN_BYTES) -> None:
    """Drain a capped amount from an HTTPError body, then close it."""
    try:
        remaining = max_bytes
        while remaining > 0:
            block = exc.read(min(8192, remaining))
            if not block:
                break
            remaining -= len(block)
    except Exception:
        pass
    try:
        exc.close()
    except Exception:
        pass


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
