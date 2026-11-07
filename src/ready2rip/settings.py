# SPDX-License-Identifier: GPL-3.0-or-later
"""Application settings (GSettings with in-memory fallback)."""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass, fields, replace
from pathlib import Path

from gi.repository import Gio, GLib

from ready2rip.paths import (
    DEFAULT_ALBUM_FOLDER_TEMPLATE,
    DEFAULT_FILENAME_TEMPLATE,
    migrate_album_folder_template,
    migrate_filename_template,
)
from ready2rip.tags.artists import (
    DEFAULT_ARTIST_STYLE,
    normalize_artist_style,
)

log = logging.getLogger(__name__)

SCHEMA_ID = 'org.ready2rip.Ready2Rip'
_SCHEMA_XML = 'org.ready2rip.Ready2Rip.gschema.xml'

ENCODERS = (
    ('wav', 'WAV (uncompressed)'),
    ('aiff', 'AIFF (uncompressed)'),
    ('flac', 'FLAC (lossless)'),
    ('opus', 'Opus'),
    ('aac', 'M4A'),
    ('mp3', 'MP3'),
)

# Secondary “Also encode lossy” codecs (lossy companions to a primary encode).
ALSO_ENCODERS = (
    ('mp3', 'MP3'),
    ('aac', 'M4A'),
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


def normalize_also_encode_format(value: str | None) -> str:
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


def opus_bitrate_label(bitrate: int) -> str:
    return f'{int(bitrate)} kbps VBR'


# 0 = keep full downloaded resolution when embedding.
# Default 1000: crisp on phones/desktops without multi-MB-per-track bloat
# (Picard CAA default download is 500; 600–800 favors old car decks).
DEFAULT_ARTWORK_MAX_SIZE = 1000
ARTWORK_SIZES = (
    (300, '300 px'),
    (500, '500 px'),
    (600, '600 px'),
    (800, '800 px'),
    (1000, '1000 px'),
    (1200, '1200 px'),
    (0, 'Original (no downscale)'),
)

# Map removed / legacy sizes to a current preset.
_ARTWORK_SIZE_ALIASES = {
    720: 800,
}

def _get_bool(settings: Gio.Settings, key: str, default: bool) -> bool:
    """Read a boolean key; fall back if the installed schema is older."""
    try:
        return settings.get_boolean(key)
    except Exception:
        return default


def _get_string(settings: Gio.Settings, key: str, default: str) -> str:
    try:
        return settings.get_string(key) or default
    except Exception:
        return default


def _get_int(settings: Gio.Settings, key: str, default: int) -> int:
    try:
        return int(settings.get_int(key))
    except Exception:
        return default


def _load_mp3_quality(settings: Gio.Settings) -> str:
    """Read mp3-quality; migrate legacy mp3-bitrate only when explicitly set."""
    try:
        user_q = settings.get_user_value('mp3-quality')
        if user_q is not None:
            return normalize_mp3_quality(user_q.get_string())
    except Exception:
        pass
    try:
        user_br = settings.get_user_value('mp3-bitrate')
        if user_br is not None:
            return mp3_quality_from_bitrate(int(user_br.get_int32()))
    except Exception:
        pass
    try:
        # Older compiled schema without mp3-quality — use bitrate as-is.
        return mp3_quality_from_bitrate(settings.get_int('mp3-bitrate'))
    except Exception:
        return 'v0'


def _load_aac_quality(settings: Gio.Settings) -> str:
    """Read aac-quality; migrate legacy aac-bitrate when needed."""
    try:
        user_q = settings.get_user_value('aac-quality')
        if user_q is not None:
            return normalize_aac_quality(user_q.get_string())
    except Exception:
        pass
    try:
        user_br = settings.get_user_value('aac-bitrate')
        if user_br is not None:
            return aac_quality_from_bitrate(int(user_br.get_int32()))
    except Exception:
        pass
    try:
        return aac_quality_from_bitrate(settings.get_int('aac-bitrate'))
    except Exception:
        return 'cbr256'


def default_music_directory() -> Path:
    """XDG Music folder, or ``~/Music`` if unset."""
    music = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_MUSIC)
    if music:
        return Path(music)
    return Path.home() / 'Music'


def default_output_directory() -> str:
    """Default rip output path as a string (for settings / UI).

    Always the user's Music directory (XDG ``DIRECTORY_MUSIC`` / ``~/Music``).
    """
    return str(default_music_directory())


@dataclass
class AppSettings:
    """Snapshot of user preferences used by the UI and rip pipeline."""

    device: str = '/dev/sr0'
    # Appearance: default (system) | light | dark — Adw.StyleManager color scheme.
    color_scheme: str = 'default'
    output_directory: str = ''  # empty → default_output_directory() (Music)
    encode_format: str = 'flac'
    flac_compression: int = 5
    # LAME preset id: v0–v5 or cbr128/192/256/320.
    mp3_quality: str = 'v0'
    aac_quality: str = 'cbr256'
    # Legacy mirror of CBR aac_quality for older code paths; prefer aac_quality.
    aac_bitrate: int = 256
    opus_bitrate: int = 160
    # Optional second encode from the same verified WAV (lossy companion).
    also_encode: bool = False
    also_encode_format: str = 'mp3'
    also_encode_folder: str = 'format_subdir'
    # Second output root when also_encode_folder is custom_folder.
    also_encode_directory: str = ''
    apply_replaygain: bool = True
    # iTunNORM Sound Check on iPod-compatible formats (MP3/M4A/AIFF/WAV).
    write_sound_check: bool = True
    # Write Encoded by / ENCODER / ©too with ready2rip + version.
    add_ripper_tag: bool = True
    embed_artwork: bool = True
    fetch_artwork: bool = True
    artwork_source_itunes: bool = True
    artwork_source_caa: bool = True
    artwork_max_size: int = DEFAULT_ARTWORK_MAX_SIZE
    use_musicbrainz: bool = True
    use_freedb: bool = True
    auto_lookup_metadata: bool = True
    filename_template: str = DEFAULT_FILENAME_TEMPLATE
    # Multi-disc albums use {disc_folder} (CD1, CD2, …); track files stay 01…
    album_folder_template: str = DEFAULT_ALBUM_FOLDER_TEMPLATE
    # How multi-artist strings appear in filenames/tags: semicolon | slash | feat.
    artist_style: str = DEFAULT_ARTIST_STYLE
    verify_accuraterip: bool = True
    verify_ctdb: bool = True
    submit_ctdb: bool = False
    drive_sample_offset: int = 0
    drive_offset_configured: bool = False
    drive_offset_device: str = ''
    burst_fallback: bool = False
    write_rip_log: bool = True
    # Multi-file .cue sheet for secure/accurate rips.
    write_cue_file: bool = True
    test_and_copy: bool = True
    rip_htoa: bool = True
    # Raw 2352-byte .bin for mixed-mode / CD-Extra data tracks.
    rip_data_tracks: bool = True
    # Continuous disc image instead of per-track files (CUE is separate).
    copy_image: bool = False
    auto_rip: bool = False
    auto_eject: bool = False

    def resolved_output_directory(self) -> Path:
        """Output folder for rips; empty setting means the Music directory."""
        raw = self.output_directory.strip()
        if raw:
            return Path(raw).expanduser()
        return default_music_directory()

    def resolved_also_encode_directory(self) -> Path:
        """Also-encode custom root; empty falls back to the primary output."""
        raw = (self.also_encode_directory or '').strip()
        if raw:
            return Path(raw).expanduser()
        return self.resolved_output_directory()


def _checkout_schema_xml() -> Path | None:
    """``data/*.gschema.xml`` when running from a source checkout."""
    # src/ready2rip/settings.py → repo root is parents[2]
    data = Path(__file__).resolve().parents[2] / 'data' / _SCHEMA_XML
    return data if data.is_file() else None


def _schema_cache_dirs() -> list[Path]:
    """Writable places to put a compiled checkout schema."""
    dirs = [Path(GLib.get_user_cache_dir()) / 'ready2rip' / 'gschemas']
    repo_cache = Path(__file__).resolve().parents[2] / '.cache' / 'gschemas'
    if repo_cache not in dirs:
        dirs.append(repo_cache)
    return dirs


def _compile_checkout_schemas() -> Path | None:
    """Compile the checkout schema (no Meson required)."""
    xml = _checkout_schema_xml()
    if xml is None:
        return None
    compiler = shutil.which('glib-compile-schemas')
    if compiler is None:
        return None
    last_exc: Exception | None = None
    for cache in _schema_cache_dirs():
        compiled = cache / 'gschemas.compiled'
        dest_xml = cache / _SCHEMA_XML
        try:
            stale = (
                not compiled.is_file()
                or not dest_xml.is_file()
                or dest_xml.stat().st_mtime < xml.stat().st_mtime
                or compiled.stat().st_mtime < xml.stat().st_mtime
            )
            if stale:
                cache.mkdir(parents=True, exist_ok=True)
                dest_xml.write_bytes(xml.read_bytes())
                subprocess.run(
                    [compiler, str(cache)],
                    check=True,
                    capture_output=True,
                    text=True,
                )
            if compiled.is_file():
                return cache
        except (OSError, subprocess.CalledProcessError) as exc:
            last_exc = exc
            continue
    if last_exc is not None:
        log.debug('Could not compile checkout GSettings schema: %s', last_exc)
    return None


def _schema_source() -> Gio.SettingsSchemaSource | None:
    """Prefer a checkout-compiled schema, then the process default source."""
    default = Gio.SettingsSchemaSource.get_default()
    cache = _compile_checkout_schemas()
    if cache is not None:
        try:
            return Gio.SettingsSchemaSource.new_from_directory(
                str(cache), default, True
            )
        except GLib.Error as exc:
            log.debug('Could not load checkout schema source: %s', exc)
    return default


class SettingsStore:
    """Read/write GSettings when the schema is available; else memory defaults."""

    def __init__(self) -> None:
        self._settings = self._open_settings()
        self._memory = AppSettings()

    @staticmethod
    def _open_settings() -> Gio.Settings | None:
        source = _schema_source()
        if source is None:
            return None
        schema = source.lookup(SCHEMA_ID, True)
        if schema is None:
            return None
        return Gio.Settings.new_full(schema, None, None)

    def get(self) -> AppSettings:
        s = self._settings
        if s is None:
            snap = replace(self._memory)
        else:
            scheme = _get_string(s, 'color-scheme', 'default')
            if scheme not in ('default', 'light', 'dark'):
                scheme = 'default'
            snap = AppSettings(
                device=s.get_string('device') or '/dev/sr0',
                color_scheme=scheme,
                output_directory=s.get_string('output-directory') or '',
                encode_format=s.get_string('encode-format') or 'flac',
                flac_compression=s.get_int('flac-compression'),
                mp3_quality=_load_mp3_quality(s),
                aac_quality=(aq := _load_aac_quality(s)),
                aac_bitrate=(
                    int(aq[3:])
                    if aq.startswith('cbr') and aq[3:].isdigit()
                    else _get_int(s, 'aac-bitrate', 256)
                ),
                opus_bitrate=s.get_int('opus-bitrate'),
                also_encode=_get_bool(s, 'also-encode', False),
                also_encode_format=normalize_also_encode_format(
                    _get_string(s, 'also-encode-format', 'mp3')
                ),
                also_encode_folder=normalize_also_encode_folder(
                    _get_string(s, 'also-encode-folder', 'format_subdir')
                ),
                also_encode_directory=_get_string(
                    s, 'also-encode-directory', ''
                ),
                apply_replaygain=s.get_boolean('apply-replaygain'),
                write_sound_check=_get_bool(s, 'write-sound-check', True),
                add_ripper_tag=_get_bool(s, 'add-ripper-tag', True),
                embed_artwork=s.get_boolean('embed-artwork'),
                fetch_artwork=s.get_boolean('fetch-artwork'),
                artwork_source_itunes=_get_bool(s, 'artwork-source-itunes', True),
                artwork_source_caa=_get_bool(s, 'artwork-source-caa', True),
                artwork_max_size=s.get_int('artwork-max-size'),
                use_musicbrainz=s.get_boolean('use-musicbrainz'),
                use_freedb=s.get_boolean('use-freedb'),
                auto_lookup_metadata=s.get_boolean('auto-lookup-metadata'),
                filename_template=migrate_filename_template(
                    s.get_string('filename-template')
                ),
                album_folder_template=migrate_album_folder_template(
                    s.get_string('album-folder-template')
                ),
                artist_style=normalize_artist_style(
                    _get_string(s, 'artist-style', DEFAULT_ARTIST_STYLE)
                ),
                verify_accuraterip=s.get_boolean('verify-accuraterip'),
                verify_ctdb=_get_bool(s, 'verify-ctdb', True),
                submit_ctdb=_get_bool(s, 'submit-ctdb', False),
                drive_sample_offset=s.get_int('drive-sample-offset'),
                drive_offset_configured=s.get_boolean('drive-offset-configured'),
                drive_offset_device=s.get_string('drive-offset-device'),
                burst_fallback=s.get_boolean('burst-fallback'),
                write_rip_log=s.get_boolean('write-rip-log'),
                write_cue_file=_get_bool(s, 'write-cue-file', True),
                test_and_copy=_get_bool(s, 'test-and-copy', True),
                rip_htoa=_get_bool(s, 'rip-htoa', True),
                rip_data_tracks=_get_bool(s, 'rip-data-tracks', True),
                copy_image=_get_bool(s, 'copy-image', False),
                auto_rip=_get_bool(s, 'auto-rip', False),
                auto_eject=_get_bool(s, 'auto-eject', False),
            )

        if not snap.output_directory.strip():
            snap.output_directory = default_output_directory()

        # Persist migrated naming defaults when the stored template was legacy.
        if s is not None:
            stored_folder = (s.get_string('album-folder-template') or '').strip()
            if snap.album_folder_template != stored_folder:
                s.set_string('album-folder-template', snap.album_folder_template)
            stored_name = (s.get_string('filename-template') or '').strip()
            if snap.filename_template != stored_name:
                s.set_string('filename-template', snap.filename_template)

        # Migrate removed artwork size presets (e.g. 720 → 800).
        allowed = {px for px, _label in ARTWORK_SIZES}
        if snap.artwork_max_size in _ARTWORK_SIZE_ALIASES:
            snap.artwork_max_size = _ARTWORK_SIZE_ALIASES[snap.artwork_max_size]
        elif snap.artwork_max_size not in allowed:
            # Drop free-form custom sizes; keep default embed size.
            snap.artwork_max_size = DEFAULT_ARTWORK_MAX_SIZE
        return snap

    def update(self, **kwargs) -> AppSettings:
        known = {f.name for f in fields(AppSettings)}
        clean = {k: v for k, v in kwargs.items() if k in known}
        current = replace(self.get(), **clean)

        s = self._settings
        if s is None:
            self._memory = current
            return current

        key_map = {
            'device': ('device', 'string'),
            'color_scheme': ('color-scheme', 'string'),
            'output_directory': ('output-directory', 'string'),
            'encode_format': ('encode-format', 'string'),
            'flac_compression': ('flac-compression', 'int'),
            'mp3_quality': ('mp3-quality', 'string'),
            'aac_quality': ('aac-quality', 'string'),
            'aac_bitrate': ('aac-bitrate', 'int'),
            'opus_bitrate': ('opus-bitrate', 'int'),
            'also_encode': ('also-encode', 'bool'),
            'also_encode_format': ('also-encode-format', 'string'),
            'also_encode_folder': ('also-encode-folder', 'string'),
            'also_encode_directory': ('also-encode-directory', 'string'),
            'apply_replaygain': ('apply-replaygain', 'bool'),
            'write_sound_check': ('write-sound-check', 'bool'),
            'add_ripper_tag': ('add-ripper-tag', 'bool'),
            'embed_artwork': ('embed-artwork', 'bool'),
            'fetch_artwork': ('fetch-artwork', 'bool'),
            'artwork_source_itunes': ('artwork-source-itunes', 'bool'),
            'artwork_source_caa': ('artwork-source-caa', 'bool'),
            'artwork_max_size': ('artwork-max-size', 'int'),
            'use_musicbrainz': ('use-musicbrainz', 'bool'),
            'use_freedb': ('use-freedb', 'bool'),
            'auto_lookup_metadata': ('auto-lookup-metadata', 'bool'),
            'filename_template': ('filename-template', 'string'),
            'album_folder_template': ('album-folder-template', 'string'),
            'artist_style': ('artist-style', 'string'),
            'verify_accuraterip': ('verify-accuraterip', 'bool'),
            'verify_ctdb': ('verify-ctdb', 'bool'),
            'submit_ctdb': ('submit-ctdb', 'bool'),
            'drive_sample_offset': ('drive-sample-offset', 'int'),
            'drive_offset_configured': ('drive-offset-configured', 'bool'),
            'drive_offset_device': ('drive-offset-device', 'string'),
            'burst_fallback': ('burst-fallback', 'bool'),
            'write_rip_log': ('write-rip-log', 'bool'),
            'write_cue_file': ('write-cue-file', 'bool'),
            'test_and_copy': ('test-and-copy', 'bool'),
            'rip_htoa': ('rip-htoa', 'bool'),
            'rip_data_tracks': ('rip-data-tracks', 'bool'),
            'copy_image': ('copy-image', 'bool'),
            'auto_rip': ('auto-rip', 'bool'),
            'auto_eject': ('auto-eject', 'bool'),
        }
        # Only write keys that were actually changed (and always-safe full sync
        # for those attributes).
        attrs = clean.keys() if clean else key_map.keys()
        for attr in attrs:
            if attr not in key_map:
                continue
            gkey, kind = key_map[attr]
            value = getattr(current, attr)
            if kind == 'string':
                if attr == 'mp3_quality':
                    value = normalize_mp3_quality(str(value))
                    current = replace(current, mp3_quality=value)
                elif attr == 'aac_quality':
                    value = normalize_aac_quality(str(value))
                    current = replace(current, aac_quality=value)
                    # Keep legacy int in sync for CBR presets.
                    if value.startswith('cbr') and value[3:].isdigit():
                        current = replace(current, aac_bitrate=int(value[3:]))
                        try:
                            s.set_int('aac-bitrate', int(value[3:]))
                        except Exception:
                            pass
                elif attr == 'artist_style':
                    value = normalize_artist_style(str(value))
                    current = replace(current, artist_style=value)
                elif attr == 'also_encode_format':
                    value = normalize_also_encode_format(str(value))
                    current = replace(current, also_encode_format=value)
                elif attr == 'also_encode_folder':
                    value = normalize_also_encode_folder(str(value))
                    current = replace(current, also_encode_folder=value)
                s.set_string(gkey, str(value))
            elif kind == 'int':
                s.set_int(gkey, int(value))
            elif kind == 'bool':
                s.set_boolean(gkey, bool(value))
        return current
