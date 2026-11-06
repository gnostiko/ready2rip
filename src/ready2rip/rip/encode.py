# SPDX-License-Identifier: GPL-3.0-or-later
"""Standard encoder command lines for CDDA WAV → FLAC / MP3 / AAC / Opus.

The ripped PCM is passed through unchanged: no gain, EQ, fade, channel remix,
or forced resample flags. Compression uses stock quality presets only.

  * FLAC — reference ``flac`` CLI; compression level only (lossless).
  * MP3  — LAME ``-V n`` / ``-b n``; ``--noreplaygain`` so LAME does not
    write competing RG tags (we tag after encode).
  * AAC  — ffmpeg ``libfdk_aac`` when available (FDK VBR 1–5 / CBR), else
    native ``aac`` with ``-q:a`` VBR (or ``-b:a`` for CBR presets).
    AAC-LC only; no HE-AAC, no custom filters.
  * Opus — ``opusenc`` VBR + ``--music`` when available; ffmpeg ``libopus``
    fallback mirrors the same bitrate / VBR / audio application settings.
    (Opus packets are always 48 kHz internally; that is the codec, not a
    Ready2Rip pre-process.)
"""

from __future__ import annotations

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


def encode_command_summary(
    fmt: str,
    *,
    flac_compression: int = 5,
    mp3_quality: str = 'v0',
    aac_quality: str = 'cbr256',
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
            return (
                f'ffmpeg -c:a libfdk_aac -afterburner 1 '
                f'({rip_log_aac_quality(q)}; stock AAC-LC)'
            )
        return (
            f'ffmpeg -c:a aac ({rip_log_aac_quality(q)}; '
            f'stock AAC-LC; no FDK)'
        )
    if fmt == 'opus':
        return (
            f'opusenc --bitrate {int(opus_bitrate)} --vbr --music '
            f'(stock Opus; no pre-process)'
        )
    if fmt == 'wav':
        return 'WAV copy (uncompressed PCM)'
    return fmt or 'unknown'
