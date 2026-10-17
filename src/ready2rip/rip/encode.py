# SPDX-License-Identifier: GPL-3.0-or-later
"""Standard encoder command lines for CDDA WAV → FLAC / MP3 / AAC / Opus.

The ripped PCM is passed through unchanged: no gain, EQ, fade, channel remix,
or forced resample flags. Compression uses stock quality presets only.

  * FLAC — reference ``flac`` CLI; compression level only (lossless).
  * MP3  — LAME ``-V n`` / ``-b n`` (Hydrogenaudio-style); ``--noreplaygain``
    so LAME does not write competing RG tags (we tag after encode).
  * AAC  — ffmpeg ``libfdk_aac`` when available (FDK VBR 1–5 / CBR), else
    native ``aac`` with the same preset names mapped to stock ``-b:a`` /
    approximate CBR for VBR. AAC-LC only; no HE-AAC, no custom filters.
  * Opus — ``opusenc`` VBR + ``--music`` when available; ffmpeg ``libopus``
    fallback mirrors the same bitrate / VBR / audio application settings.
    (Opus packets are always 48 kHz internally; that is the codec, not a
    Ready2Rip pre-process.)
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from ready2rip.settings import (
    lame_args_for_mp3_quality,
    normalize_aac_quality,
    normalize_mp3_quality,
    rip_log_aac_quality,
)

# FDK VBR mode → approximate CBR (kbps) when only native ffmpeg ``aac`` exists.
_AAC_VBR_TO_NATIVE_CBR = {
    5: 192,
    4: 128,
    3: 96,
    2: 64,
    1: 48,
}


def flac_encode_argv(wav_path: Path, out_path: Path, compression: int) -> list[str]:
    """``flac -<0..8>`` — bit-identical PCM, size/speed tradeoff only."""
    level = max(0, min(8, int(compression)))
    return [
        'flac',
        f'-{level}',
        '--silent',
        '-o',
        str(out_path),
        str(wav_path),
    ]


def mp3_encode_argv(wav_path: Path, out_path: Path, quality: str) -> list[str]:
    """Stock LAME VBR (``-V``) or CBR (``-b``); no resample/scale/filter."""
    return [
        'lame',
        '--silent',
        '--noreplaygain',
        *lame_args_for_mp3_quality(normalize_mp3_quality(quality)),
        str(wav_path),
        str(out_path),
    ]


def prefer_fdk_aac() -> bool:
    """True when ffmpeg was built with Fraunhofer FDK AAC."""
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
    q = normalize_aac_quality(quality)
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
            # Native aac has no FDK-compatible VBR modes — use approx CBR.
            br = _AAC_VBR_TO_NATIVE_CBR.get(mode, 192)
            cmd.extend(['-b:a', f'{br}k'])
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
        return f'flac -{level} (lossless; PCM unaltered)'
    if fmt == 'mp3':
        q = normalize_mp3_quality(mp3_quality)
        args = ' '.join(lame_args_for_mp3_quality(q))
        return f'lame --noreplaygain {args} (stock LAME; no resample/scale)'
    if fmt == 'aac':
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
