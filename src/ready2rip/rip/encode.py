# SPDX-License-Identifier: GPL-3.0-or-later
"""Standard encoder command lines for CDDA WAV → FLAC / MP3 / AAC / Opus / QAAC.

The ripped PCM is passed through unchanged: no gain, EQ, fade, channel remix,
or forced resample flags. Compression uses stock quality presets only.

  * FLAC — reference ``flac`` CLI; compression level only (lossless).
  * MP3  — LAME ``-V n`` / ``-b n``; ``--noreplaygain`` so LAME does not
    write competing RG tags (we tag after encode).
  * AAC  — ffmpeg ``libfdk_aac`` when available (FDK VBR 1–5 / CBR), else
    native ``aac`` with ``-q:a`` VBR (or ``-b:a`` for CBR presets).
    AAC-LC only; no HE-AAC, no custom filters.
  * QAAC — Apple AAC via ``qaac`` / Wine (opt-in; never bundled). Popular
    TVBR / CVBR / CBR presets; ``--quality 2``.
  * Opus — ``opusenc`` VBR + ``--music`` when available; ffmpeg ``libopus``
    fallback mirrors the same bitrate / VBR / audio application settings.
    (Opus packets are always 48 kHz internally; that is the codec, not a
    Ready2Rip pre-process.)
  * AIFF — ffmpeg ``pcm_s16be`` classic AIFF (bit-identical CDDA PCM).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

# Preset IDs mirrored from settings (keep encode helpers gi-free for CI).
_AAC_QUALITY_IDS = frozenset(
    {
        'vbr1',
        'vbr2',
        'vbr3',
        'vbr4',
        'vbr5',
        'cbr128',
        'cbr192',
        'cbr256',
        'cbr320',
    }
)

_QAAC_QUALITY_IDS = frozenset(
    {
        'tvbr82',
        'tvbr91',
        'tvbr100',
        'cvbr256',
        'cbr256',
    }
)

# FDK VBR 1–5 → ffmpeg native ``aac`` VBR quality scale (``-q:a``).
# Higher values ≈ higher bitrate / quality for the native encoder.
_AAC_VBR_TO_NATIVE_Q = {
    1: 0.5,
    2: 0.9,
    3: 1.3,
    4: 1.7,
    5: 2.0,
}


def _normalize_aac_quality(value: str | None) -> str:
    raw = (value or '').strip().lower()
    if raw in _AAC_QUALITY_IDS:
        return raw
    return 'cbr256'


def _normalize_qaac_quality(value: str | None) -> str:
    raw = (value or '').strip().lower()
    if raw in _QAAC_QUALITY_IDS:
        return raw
    return 'cvbr256'


def _qaac_wrapper_candidates() -> list[Path]:
    """Likely host wrapper scripts (GUI launches often omit ``~/.local/bin``)."""
    home = Path.home()
    bin_dirs: list[Path] = []
    xdg_bin = (os.environ.get('XDG_BIN_HOME') or '').strip()
    if xdg_bin:
        bin_dirs.append(Path(xdg_bin).expanduser())
    bin_dirs.extend((home / '.local' / 'bin', home / 'bin'))
    names = ('qaac', 'qaac64')
    out: list[Path] = []
    seen: set[Path] = set()
    for directory in bin_dirs:
        for name in names:
            path = directory / name
            if path in seen:
                continue
            seen.add(path)
            out.append(path)
    return out


def _qaac_wine_exe_candidates() -> list[tuple[Path | None, Path]]:
    """``(WINEPREFIX or None, qaac*.exe)`` pairs for known Wine layouts.

    Prefer the dedicated Fedora ``qaac-wine`` prefix (needs ``WINEPREFIX``) over
    the default ``~/.wine`` tree.
    """
    home = Path.home()
    qaac_wine = home / '.local' / 'share' / 'qaac-wine' / 'prefix'
    default = home / '.wine'
    return [
        (qaac_wine, qaac_wine / 'drive_c' / 'qaac' / 'qaac64.exe'),
        (qaac_wine, qaac_wine / 'drive_c' / 'qaac' / 'qaac.exe'),
        (None, default / 'drive_c' / 'qaac' / 'qaac64.exe'),
        (None, default / 'drive_c' / 'qaac' / 'qaac.exe'),
    ]


@lru_cache(maxsize=1)
def resolve_qaac_command() -> tuple[str, ...] | None:
    """Argv prefix that runs qaac, or None when Wine/qaac/DLLs are not set up.

    Lookup order:
      1. ``READY2RIP_QAAC`` or ``QAAC`` (wrapper script or ``qaac.exe``)
      2. ``qaac`` / ``qaac64`` on ``PATH``
      3. ``~/.local/bin/qaac`` (and friends) even when not on ``PATH``
      4. ``wine`` + known prefixes (``qaac-wine``, then ``~/.wine``)
    """
    for env_key in ('READY2RIP_QAAC', 'QAAC'):
        raw = (os.environ.get(env_key) or '').strip()
        if not raw:
            continue
        path = Path(raw).expanduser()
        if not path.is_file():
            continue
        if path.suffix.lower() == '.exe':
            wine = shutil.which('wine')
            if wine:
                return (wine, str(path))
            continue
        return (str(path),)

    for name in ('qaac', 'qaac64'):
        found = shutil.which(name)
        if found:
            return (found,)

    for wrapper in _qaac_wrapper_candidates():
        if wrapper.is_file() and os.access(wrapper, os.X_OK):
            return (str(wrapper),)

    wine = shutil.which('wine')
    if wine:
        for wineprefix, exe in _qaac_wine_exe_candidates():
            if not exe.is_file():
                continue
            if wineprefix is not None:
                # Dedicated prefix (e.g. install-qaac-fedora.sh) must be set.
                return ('env', f'WINEPREFIX={wineprefix}', wine, str(exe))
            return (wine, str(exe))
    return None


def qaac_available() -> bool:
    """True when a qaac invoke path was resolved (not a full ``--check``)."""
    return resolve_qaac_command() is not None


def flac_encode_argv(
    wav_path: Path,
    out_path: Path,
    compression: int,
    *,
    verify: bool = True,
) -> list[str]:
    """``flac -<0..8>`` — bit-identical PCM, size/speed tradeoff only.

    ``--verify`` re-decodes while encoding so encoder bugs fail the rip
    instead of writing a bad archive file.
    """
    level = max(0, min(8, int(compression)))
    cmd = [
        'flac',
        f'-{level}',
        '--silent',
    ]
    if verify:
        cmd.append('--verify')
    cmd.extend(['-o', str(out_path), str(wav_path)])
    return cmd


def mp3_encode_argv(wav_path: Path, out_path: Path, quality: str) -> list[str]:
    """Stock LAME VBR (``-V``) or CBR (``-b``); no resample/scale/filter."""
    from ready2rip.settings import lame_args_for_mp3_quality, normalize_mp3_quality

    return [
        'lame',
        '--silent',
        '--noreplaygain',
        *lame_args_for_mp3_quality(normalize_mp3_quality(quality)),
        str(wav_path),
        str(out_path),
    ]


@lru_cache(maxsize=1)
def prefer_fdk_aac() -> bool:
    """True when ffmpeg was built with Fraunhofer FDK AAC (cached per process)."""
    ffmpeg = shutil.which('ffmpeg')
    if ffmpeg is None:
        return False
    try:
        proc = subprocess.run(
            [ffmpeg, '-hide_banner', '-encoders'],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    blob = (proc.stdout or '') + (proc.stderr or '')
    return 'libfdk_aac' in blob


def aac_encode_argv(
    wav_path: Path,
    out_path: Path,
    quality: str,
    *,
    use_fdk: bool | None = None,
) -> list[str]:
    """Stock AAC → ``.m4a`` via ffmpeg (FDK preferred).

    Presets match the Encoding panel (VBR 1–5 / CBR). No resample, profile
    override, or psychoacoustic tweaks beyond encoder defaults.
    """
    q = _normalize_aac_quality(quality)
    if use_fdk is None:
        use_fdk = prefer_fdk_aac()
    codec = 'libfdk_aac' if use_fdk else 'aac'

    cmd = [
        'ffmpeg',
        '-y',
        '-loglevel',
        'error',
        '-i',
        str(wav_path),
        '-c:a',
        codec,
    ]

    if use_fdk:
        # FDK afterburner: improved quality analysis (explicit; default is 1).
        cmd.extend(['-afterburner', '1'])

    if q.startswith('vbr') and q[3:].isdigit():
        mode = max(1, min(5, int(q[3:])))
        if use_fdk:
            cmd.extend(['-vbr', str(mode)])
        else:
            # Native aac VBR via quality scale (not a CBR bitrate stand-in).
            qscale = _AAC_VBR_TO_NATIVE_Q.get(mode, 1.3)
            cmd.extend(['-q:a', str(qscale)])
    elif q.startswith('cbr') and q[3:].isdigit():
        cmd.extend(['-b:a', f'{int(q[3:])}k'])
    else:
        cmd.extend(['-b:a', '256k'])

    # Container hint only (moov at start); does not alter audio samples.
    cmd.extend(['-movflags', '+faststart', str(out_path)])
    return cmd


def qaac_encode_argv(
    wav_path: Path,
    out_path: Path,
    quality: str,
    *,
    command: tuple[str, ...] | None = None,
) -> list[str]:
    """Apple AAC via qaac (often under Wine) → ``.m4a``.

    Popular TVBR / CVBR / CBR presets only; ``--quality 2``. Tags are written
    afterward by Ready2Rip (mutagen), not by qaac.
    """
    prefix = command if command is not None else resolve_qaac_command()
    if not prefix:
        raise RuntimeError(
            'qaac not found (install Wine + qaac.exe + Apple AAC codecs, or set READY2RIP_QAAC)'
        )
    q = _normalize_qaac_quality(quality)
    cmd = list(prefix)
    if q.startswith('tvbr') and q[4:].isdigit():
        cmd.extend(['--tvbr', q[4:]])
    elif q.startswith('cvbr') and q[4:].isdigit():
        cmd.extend(['--cvbr', q[4:]])
    elif q.startswith('cbr') and q[3:].isdigit():
        cmd.extend(['--cbr', q[3:]])
    else:
        cmd.extend(['--cvbr', '256'])
    cmd.extend(['--quality', '2', '-o', str(out_path), str(wav_path)])
    return cmd


def opus_encode_argv(wav_path: Path, out_path: Path, bitrate_kbps: int) -> list[str]:
    """``opusenc`` standard VBR music encode."""
    bitrate = max(6, min(512, int(bitrate_kbps)))
    return [
        'opusenc',
        '--quiet',
        '--bitrate',
        str(bitrate),
        '--vbr',
        '--music',
        str(wav_path),
        str(out_path),
    ]


def opus_ffmpeg_argv(wav_path: Path, out_path: Path, bitrate_kbps: int) -> list[str]:
    """ffmpeg ``libopus`` fallback matching opusenc VBR / music defaults."""
    bitrate = max(6, min(512, int(bitrate_kbps)))
    return [
        'ffmpeg',
        '-y',
        '-loglevel',
        'error',
        '-i',
        str(wav_path),
        '-c:a',
        'libopus',
        '-b:a',
        f'{bitrate}k',
        '-vbr',
        'on',
        '-application',
        'audio',
        '-compression_level',
        '10',
        str(out_path),
    ]


def aiff_encode_argv(wav_path: Path, out_path: Path) -> list[str]:
    """Classic AIFF (big-endian 16-bit PCM) via ffmpeg — no resample/dither."""
    return [
        'ffmpeg',
        '-y',
        '-loglevel',
        'error',
        '-i',
        str(wav_path),
        '-c:a',
        'pcm_s16be',
        '-f',
        'aiff',
        str(out_path),
    ]


def encode_command_summary(
    fmt: str,
    *,
    flac_compression: int = 5,
    mp3_quality: str = 'v0',
    aac_quality: str = 'cbr256',
    qaac_quality: str = 'cvbr256',
    opus_bitrate: int = 160,
) -> str:
    """One-line description for the rip log."""
    fmt = (fmt or '').strip().lower()
    if fmt == 'flac':
        level = max(0, min(8, int(flac_compression)))
        return f'flac -{level} --verify (lossless; PCM unaltered)'
    if fmt == 'mp3':
        from ready2rip.settings import lame_args_for_mp3_quality, normalize_mp3_quality

        q = normalize_mp3_quality(mp3_quality)
        args = ' '.join(lame_args_for_mp3_quality(q))
        return f'lame --noreplaygain {args} (stock LAME; no resample/scale)'
    if fmt == 'aac':
        from ready2rip.settings import normalize_aac_quality, rip_log_aac_quality

        q = normalize_aac_quality(aac_quality)
        if prefer_fdk_aac():
            return f'ffmpeg -c:a libfdk_aac -afterburner 1 ({rip_log_aac_quality(q)}; stock AAC-LC)'
        return f'ffmpeg -c:a aac ({rip_log_aac_quality(q)}; stock AAC-LC; no FDK)'
    if fmt == 'qaac':
        from ready2rip.settings import normalize_qaac_quality, rip_log_qaac_quality

        q = normalize_qaac_quality(qaac_quality)
        return f'qaac --quality 2 ({rip_log_qaac_quality(q)}; Apple AAC-LC)'
    if fmt == 'opus':
        return f'opusenc --bitrate {int(opus_bitrate)} --vbr --music (stock Opus; no pre-process)'
    if fmt == 'wav':
        return 'WAV copy (uncompressed PCM)'
    if fmt == 'aiff':
        return 'ffmpeg -c:a pcm_s16be -f aiff (classic AIFF; PCM unaltered)'
    return fmt or 'unknown'


def _run_version_text(argv: list[str], *, timeout: float = 20.0) -> str:
    try:
        proc = subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ''
    return ((proc.stdout or '') + '\n' + (proc.stderr or '')).strip()


def _first_nonempty_line(text: str) -> str:
    for line in (text or '').splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ''


_RE_LAME_VER = re.compile(r'LAME(?:\s+\S+)?\s+version\s+(\d+\.\d+(?:\.\d+)?)', re.I)
_RE_OPUS_VER = re.compile(r'opus(?:enc|-tools)\s+(\d+\.\d+(?:\.\d+)?)', re.I)
_RE_QAAC_VER = re.compile(
    r'(qaac(?:64)?\s+\d+\.\d+(?:\.\d+)?)(?:,\s*(CoreAudioToolbox\s+\S+))?',
    re.I,
)


@lru_cache(maxsize=1)
def _ffmpeg_version_label() -> str:
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        return 'ffmpeg'
    parts = _first_nonempty_line(_run_version_text([ffmpeg, '-version'])).split()
    if len(parts) >= 3 and parts[0].lower() == 'ffmpeg' and parts[1] == 'version':
        ver = parts[2].split('-', 1)[0]
        return f'ffmpeg {ver}' if ver else 'ffmpeg'
    return 'ffmpeg'


@lru_cache(maxsize=1)
def _flac_version_label() -> str:
    flac = shutil.which('flac')
    if not flac:
        return 'flac'
    return _first_nonempty_line(_run_version_text([flac, '--version'])) or 'flac'


@lru_cache(maxsize=1)
def _lame_version_label() -> str:
    lame = shutil.which('lame')
    if not lame:
        return 'LAME'
    blob = _run_version_text([lame, '--version'])
    match = _RE_LAME_VER.search(blob)
    if match:
        return f'LAME {match.group(1)}'
    return _first_nonempty_line(blob) or 'LAME'


@lru_cache(maxsize=1)
def _opusenc_version_label() -> str:
    opusenc = shutil.which('opusenc')
    if not opusenc:
        return 'opusenc'
    blob = _run_version_text([opusenc, '--version'])
    match = _RE_OPUS_VER.search(blob)
    if match:
        return f'opusenc {match.group(1)}'
    return _first_nonempty_line(blob) or 'opusenc'


@lru_cache(maxsize=1)
def _qaac_version_label() -> str:
    prefix = resolve_qaac_command()
    if not prefix:
        return 'qaac'
    # Wine / Mesa often spam stderr before the real qaac banner.
    for line in _run_version_text([*prefix, '--check'], timeout=60.0).splitlines():
        match = _RE_QAAC_VER.search(line.strip())
        if not match:
            continue
        base = match.group(1).strip()
        toolbox = (match.group(2) or '').strip()
        return f'{base} ({toolbox})' if toolbox else base
    return 'qaac'


@lru_cache(maxsize=16)
def encoder_tag_for_format(fmt: str) -> str:
    """Encoded-by string: encoder name + version for *fmt* (always written)."""
    key = (fmt or '').strip().lower()
    if key == 'flac':
        return _flac_version_label()
    if key == 'mp3':
        return _lame_version_label()
    if key == 'aac':
        codec = 'libfdk_aac' if prefer_fdk_aac() else 'aac'
        return f'{_ffmpeg_version_label()} ({codec})'
    if key == 'qaac':
        return _qaac_version_label()
    if key == 'opus':
        if shutil.which('opusenc'):
            return _opusenc_version_label()
        return f'{_ffmpeg_version_label()} (libopus)'
    if key == 'aiff':
        return f'{_ffmpeg_version_label()} (pcm_s16be)'
    if key == 'wav':
        return 'WAV (PCM)'
    return key or 'unknown'
