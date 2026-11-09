# SPDX-License-Identifier: GPL-3.0-or-later
"""Preference constants and pure helpers (no PyGObject — safe for CI unit tests)."""

from __future__ import annotations

# Primary rip formats — lossless / PCM only.
ENCODERS = (
    ('wav', 'WAV (uncompressed)'),
    ('aiff', 'AIFF (uncompressed)'),
    ('flac', 'FLAC (lossless)'),
)

ENCODER_IDS = frozenset(e[0] for e in ENCODERS)

# Secondary “Also encode lossy” codecs (lossy companions to a primary encode).
ALSO_ENCODERS = (
    ('mp3', 'LAME MP3'),
    ('aac', 'FDK AAC'),
    ('qaac', 'QAAC'),
    ('opus', 'Opus'),
)

ALSO_ENCODER_IDS = frozenset(e[0] for e in ALSO_ENCODERS)

# Where secondary encodes are written relative to the album folder.
ALSO_FOLDER_LAYOUTS = (
    ('format_subdir', 'Subfolder by format'),
    ('same_folder', 'Same album folder'),
    ('custom_folder', 'Custom folder'),
)

ALSO_FOLDER_LAYOUT_IDS = frozenset(e[0] for e in ALSO_FOLDER_LAYOUTS)

# How lyrics are stored when “Save lyrics (lossless)” is on.
LYRICS_SAVE_MODES = (
    ('both', 'Embed and sidecar'),
    ('embed', 'Embed in tags'),
    ('sidecar', 'Sidecar files only'),
)
LYRICS_SAVE_MODE_IDS = frozenset(m[0] for m in LYRICS_SAVE_MODES)
DEFAULT_LYRICS_SAVE_MODE = 'both'


def normalize_lyrics_save_mode(value: str | None) -> str:
    raw = (value or '').strip().lower()
    if raw in LYRICS_SAVE_MODE_IDS:
        return raw
    return DEFAULT_LYRICS_SAVE_MODE


def lyrics_mode_writes_tags(mode: str | None) -> bool:
    return normalize_lyrics_save_mode(mode) in ('embed', 'both')


def lyrics_mode_writes_sidecar(mode: str | None) -> bool:
    return normalize_lyrics_save_mode(mode) in ('sidecar', 'both')


def normalize_also_encode_format(value: str | None) -> str:
    """Secondary encode is always a lossy companion codec."""
    raw = (value or '').strip().lower()
    if raw in ALSO_ENCODER_IDS:
        return raw
    return 'mp3'


def normalize_also_encode_folder(value: str | None) -> str:
    raw = (value or '').strip().lower()
    if raw in ALSO_FOLDER_LAYOUT_IDS:
        return raw
    return 'format_subdir'


# FLAC compression levels shown in the Encoding panel.
FLAC_LEVELS = (
    (0, '0 (Fastest)'),
    (1, '1'),
    (2, '2'),
    (3, '3'),
    (4, '4'),
    (5, '5 (Default)'),
    (6, '6'),
    (7, '7'),
    (8, '8 (smallest)'),
)

# LAME presets: VBR (-V n) and CBR (-b n) — lowest quality → highest.
# (LAME V5 is the lowest VBR quality here; V0 is the highest.)
MP3_QUALITIES = (
    ('v5', 'V5 (VBR)'),
    ('v4', 'V4 (VBR)'),
    ('v3', 'V3 (VBR)'),
    ('v2', 'V2 (VBR)'),
    ('v1', 'V1 (VBR)'),
    ('v0', 'V0 (VBR)'),
    ('cbr128', '128 kbps CBR'),
    ('cbr192', '192 kbps CBR'),
    ('cbr256', '256 kbps CBR'),
    ('cbr320', '320 kbps CBR'),
)

MP3_QUALITY_IDS = frozenset(q[0] for q in MP3_QUALITIES)

# Opus VBR target bitrates (kbps) — lowest → highest.
OPUS_BITRATES = (96, 128, 160, 192, 256)

# Fraunhofer FDK AAC presets (VBR 1–5) + CBR — lowest quality → highest.
# Native ffmpeg ``aac`` uses ``-q:a`` for VBR presets when FDK is absent.
AAC_QUALITIES = (
    ('vbr1', 'VBR 1'),
    ('vbr2', 'VBR 2'),
    ('vbr3', 'VBR 3'),
    ('vbr4', 'VBR 4'),
    ('vbr5', 'VBR 5 (highest)'),
    ('cbr128', '128 kbps CBR'),
    ('cbr192', '192 kbps CBR'),
    ('cbr256', '256 kbps CBR'),
    ('cbr320', '320 kbps CBR'),
)

AAC_QUALITY_IDS = frozenset(q[0] for q in AAC_QUALITIES)

# Popular qaac presets (Apple AAC via Wine) — lowest → highest within each mode.
QAAC_QUALITIES = (
    ('tvbr82', 'TVBR 82 (~192 kbps)'),
    ('tvbr91', 'TVBR 91 (~224 kbps)'),
    ('tvbr100', 'TVBR 100 (~256 kbps)'),
    ('cvbr256', 'CVBR 256 kbps'),
    ('cbr256', 'CBR 256 kbps'),
)

QAAC_QUALITY_IDS = frozenset(q[0] for q in QAAC_QUALITIES)


def normalize_mp3_quality(value: str | None) -> str:
    raw = (value or '').strip().lower()
    if raw in MP3_QUALITY_IDS:
        return raw
    return 'v0'


def mp3_quality_from_bitrate(bitrate: int) -> str:
    """Map a legacy CBR bitrate setting to an mp3-quality id."""
    mapping = {
        320: 'cbr320',
        256: 'cbr256',
        192: 'cbr192',
        128: 'cbr128',
    }
    return mapping.get(int(bitrate), 'cbr320')


def lame_args_for_mp3_quality(quality: str) -> list[str]:
    """LAME flags for a stored mp3-quality id (without input/output paths)."""
    q = normalize_mp3_quality(quality)
    if q.startswith('v') and q[1:].isdigit():
        return ['-V', q[1:]]
    if q.startswith('cbr'):
        br = q[3:]
        if br.isdigit():
            return ['-b', br]
    return ['-V', '0']


def rip_log_mp3_quality(quality: str) -> str:
    """Human-readable MP3 setting for the rip log."""
    q = normalize_mp3_quality(quality)
    if q.startswith('v') and q[1:].isdigit():
        return f'LAME -V{q[1:]} (VBR)'
    if q.startswith('cbr') and q[3:].isdigit():
        return f'{q[3:]} kBit/s CBR'
    return q.upper()


def normalize_aac_quality(value: str | None) -> str:
    raw = (value or '').strip().lower()
    if raw in AAC_QUALITY_IDS:
        return raw
    return 'cbr256'


def aac_quality_from_bitrate(bitrate: int) -> str:
    """Map a legacy AAC CBR bitrate to an aac-quality id."""
    mapping = {
        320: 'cbr320',
        256: 'cbr256',
        192: 'cbr192',
        128: 'cbr128',
        160: 'cbr192',
        96: 'cbr128',
    }
    return mapping.get(int(bitrate), 'cbr256')


def rip_log_aac_quality(quality: str) -> str:
    """Human-readable AAC setting for the rip log."""
    q = normalize_aac_quality(quality)
    if q.startswith('vbr') and q[3:].isdigit():
        return f'FDK AAC VBR {q[3:]}'
    if q.startswith('cbr') and q[3:].isdigit():
        return f'{q[3:]} kBit/s CBR AAC'
    return q.upper()


def normalize_qaac_quality(value: str | None) -> str:
    raw = (value or '').strip().lower()
    if raw in QAAC_QUALITY_IDS:
        return raw
    return 'cvbr256'


def rip_log_qaac_quality(quality: str) -> str:
    """Human-readable QAAC setting for the rip log."""
    q = normalize_qaac_quality(quality)
    if q.startswith('tvbr') and q[4:].isdigit():
        return f'qaac TVBR {q[4:]}'
    if q.startswith('cvbr') and q[4:].isdigit():
        return f'qaac CVBR {q[4:]} kBit/s'
    if q.startswith('cbr') and q[3:].isdigit():
        return f'qaac CBR {q[3:]} kBit/s'
    return f'qaac {q.upper()}'


def opus_bitrate_label(bitrate: int) -> str:
    return f'{int(bitrate)} kbps VBR'


# Embed sizes (≤800 px). Lossy and lossless each have their own setting.
DEFAULT_ARTWORK_MAX_SIZE = 600
ARTWORK_SIZES = (
    (300, '300 px'),
    (500, '500 px'),
    (600, '600 px'),
    (800, '800 px'),
)
ARTWORK_SIZE_IDS = frozenset(px for px, _label in ARTWORK_SIZES)

# Map removed / oversized legacy presets down to the 800 px ceiling.
_ARTWORK_SIZE_ALIASES = {
    720: 800,
    1000: 800,
    1200: 800,
    0: 800,
}


def normalize_artwork_max_size(value: int | None) -> int:
    """Clamp / migrate an embed-size preference to a known preset."""
    try:
        raw = int(value) if value is not None else DEFAULT_ARTWORK_MAX_SIZE
    except (TypeError, ValueError):
        return DEFAULT_ARTWORK_MAX_SIZE
    if raw in _ARTWORK_SIZE_ALIASES:
        return _ARTWORK_SIZE_ALIASES[raw]
    if raw in ARTWORK_SIZE_IDS:
        return raw
    return DEFAULT_ARTWORK_MAX_SIZE
