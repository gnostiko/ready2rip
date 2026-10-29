# SPDX-License-Identifier: GPL-3.0-or-later
"""Application settings (GSettings with in-memory fallback)."""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass, fields, replace
from pathlib import Path

from gi.repository import Gio, GLib

log = logging.getLogger(__name__)

SCHEMA_ID = 'org.ready2rip.Ready2Rip'
_SCHEMA_XML = 'org.ready2rip.Ready2Rip.gschema.xml'

ENCODERS = (
    ('flac', 'FLAC (lossless)'),
    ('mp3', 'MP3'),
    ('aac', 'AAC (M4A)'),
    ('opus', 'Opus'),
    ('wav', 'WAV (uncompressed)'),
)

ENCODER_IDS = frozenset(e[0] for e in ENCODERS)

# FLAC compression levels shown in the Encoding panel.
FLAC_LEVELS = (
    (0, 'Fastest'),
    (1, '1'),
    (2, '2'),
    (3, '3'),
    (4, '4'),
    (5, 'Default'),
    (6, '6'),
    (7, '7'),
    (8, 'Smallest'),
)

# LAME presets: VBR (-V n) and CBR (-b n). Default V0 matches common archival rips.
MP3_QUALITIES = (
    ('v0', 'V0 (VBR)'),
    ('v1', 'V1 (VBR)'),
    ('v2', 'V2 (VBR)'),
    ('v3', 'V3 (VBR)'),
    ('v4', 'V4 (VBR)'),
    ('v5', 'V5 (VBR)'),
    ('cbr320', '320 kbps CBR'),
    ('cbr256', '256 kbps CBR'),
    ('cbr192', '192 kbps CBR'),
    ('cbr128', '128 kbps CBR'),
)

MP3_QUALITY_IDS = frozenset(q[0] for q in MP3_QUALITIES)

# Opus VBR target bitrates (kbps) — compact preset list.
OPUS_BITRATES = (96, 128, 160, 192, 256)

# Fraunhofer FDK AAC presets (VBR 1–5) + CBR — same style as MP3_QUALITIES.
# Native ffmpeg ``aac`` maps VBR presets to approximate CBR when FDK is absent.
AAC_QUALITIES = (
    ('vbr5', 'VBR 5 (highest)'),
    ('vbr4', 'VBR 4'),
    ('vbr3', 'VBR 3'),
    ('vbr2', 'VBR 2'),
    ('vbr1', 'VBR 1'),
    ('cbr320', '320 kbps CBR'),
    ('cbr256', '256 kbps CBR'),
    ('cbr192', '192 kbps CBR'),
    ('cbr128', '128 kbps CBR'),
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


@dataclass(frozen=True)
class EncodeTarget:
    """One encode destination: format plus optional output folder.

    Empty *directory* means “use the primary Output folder”.
    """

    format: str
    directory: str = ''


def encode_target_to_spec(target: EncodeTarget) -> str:
    return f'{target.format}|{target.directory}'


def encode_target_from_spec(spec: str) -> EncodeTarget | None:
    raw = (spec or '').strip()
    if not raw:
        return None
    if '|' in raw:
        fmt, _, directory = raw.partition('|')
    else:
        fmt, directory = raw, ''
    fmt = fmt.strip().lower()
    if fmt not in ENCODER_IDS:
        return None
    return EncodeTarget(format=fmt, directory=directory.strip())


def encode_targets_from_specs(specs: list[str] | tuple[str, ...]) -> tuple[EncodeTarget, ...]:
    out: list[EncodeTarget] = []
    seen: set[tuple[str, str]] = set()
    for spec in specs:
        target = encode_target_from_spec(spec)
        if target is None:
            continue
        key = (target.format, target.directory)
        if key in seen:
            continue
        seen.add(key)
        out.append(target)
    return tuple(out)

# 0 = keep full downloaded resolution when embedding
ARTWORK_SIZES = (
    (300, '300 px'),
    (500, '500 px'),
    (600, '600 px (default)'),
    (800, '800 px'),
    (1000, '1000 px'),
    (1200, '1200 px'),
    (0, 'Original (no downscale)'),
)

# Map removed / legacy sizes to a current preset.
_ARTWORK_SIZE_ALIASES = {
    720: 800,
}

DEFAULT_ALBUM_FOLDER_TEMPLATE = '{album_artist}/{dated_album}/{disc_folder}'
_LEGACY_ALBUM_FOLDER_TEMPLATES = {
    '{album_artist}/{album}/{disc_folder}',
    '{album_artist}/{album}',
    '{album_artist}/({date}) - {album}/{disc_folder}',
    '{album_artist}/({date}) - {album}',
}


def _migrate_album_folder_template(template: str | None) -> str:
    text = (template or '').strip()
    if not text or text in _LEGACY_ALBUM_FOLDER_TEMPLATES:
        return DEFAULT_ALBUM_FOLDER_TEMPLATE
    return text


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


def _get_strv(settings: Gio.Settings, key: str) -> list[str]:
    try:
        return list(settings.get_strv(key) or [])
    except Exception:
        return []


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
    # Extra formats beyond encode_format; empty directory → primary Output.
    extra_encode_targets: tuple[EncodeTarget, ...] = ()
    flac_compression: int = 5
    # LAME preset id: v0–v5 or cbr128/192/256/320.
    mp3_quality: str = 'v0'
    aac_quality: str = 'cbr256'
    # Legacy mirror of CBR aac_quality for older code paths; prefer aac_quality.
    aac_bitrate: int = 256
    opus_bitrate: int = 160
    apply_replaygain: bool = True
    embed_artwork: bool = True
    fetch_artwork: bool = True
    artwork_source_itunes: bool = True
    artwork_source_caa: bool = True
    artwork_max_size: int = 600
    use_musicbrainz: bool = True
    use_freedb: bool = True
    auto_lookup_metadata: bool = True
    filename_template: str = '{track:02d} - {title}'
    # {disc_folder} expands to CD1, CD2, … only when total discs > 1.
    album_folder_template: str = '{album_artist}/{dated_album}/{disc_folder}'
    verify_accuraterip: bool = True
    drive_sample_offset: int = 0
    drive_offset_configured: bool = False
    drive_offset_device: str = ''
    burst_fallback: bool = False
    write_rip_log: bool = True
    # Multi-file .cue sheet for secure/accurate rips.
    write_cue_file: bool = True
    test_and_copy: bool = True
    defeat_audio_cache: bool = True
    drive_cache_configured: bool = False
    drive_caches_audio: bool = False
    drive_cache_message: str = ''
    drive_accurate_stream_configured: bool = False
    drive_accurate_stream: bool = False
    drive_accurate_stream_message: str = ''
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

    def resolved_encode_targets(self) -> list[tuple[str, Path]]:
        """``(format, base_dir)`` list: primary first, then extras (no dupes)."""
        primary_fmt = (self.encode_format or 'flac').strip().lower()
        if primary_fmt not in ENCODER_IDS:
            primary_fmt = 'flac'
        primary_dir = self.resolved_output_directory()
        targets: list[tuple[str, Path]] = [(primary_fmt, primary_dir)]
        seen = {(primary_fmt, str(primary_dir))}
        for extra in self.extra_encode_targets:
            fmt = (extra.format or '').strip().lower()
            if fmt not in ENCODER_IDS:
                continue
            raw = (extra.directory or '').strip()
            base = Path(raw).expanduser() if raw else primary_dir
            key = (fmt, str(base))
            if key in seen:
                continue
            seen.add(key)
            targets.append((fmt, base))
        return targets


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
                extra_encode_targets=encode_targets_from_specs(
                    _get_strv(s, 'extra-encode-targets')
                ),
                flac_compression=s.get_int('flac-compression'),
                mp3_quality=_load_mp3_quality(s),
                aac_quality=(aq := _load_aac_quality(s)),
                aac_bitrate=(
                    int(aq[3:])
                    if aq.startswith('cbr') and aq[3:].isdigit()
                    else _get_int(s, 'aac-bitrate', 256)
                ),
                opus_bitrate=s.get_int('opus-bitrate'),
                apply_replaygain=s.get_boolean('apply-replaygain'),
                embed_artwork=s.get_boolean('embed-artwork'),
                fetch_artwork=s.get_boolean('fetch-artwork'),
                artwork_source_itunes=_get_bool(s, 'artwork-source-itunes', True),
                artwork_source_caa=_get_bool(s, 'artwork-source-caa', True),
                artwork_max_size=s.get_int('artwork-max-size'),
                use_musicbrainz=s.get_boolean('use-musicbrainz'),
                use_freedb=s.get_boolean('use-freedb'),
                auto_lookup_metadata=s.get_boolean('auto-lookup-metadata'),
                filename_template=s.get_string('filename-template')
                or '{track:02d} - {title}',
                album_folder_template=_migrate_album_folder_template(
                    s.get_string('album-folder-template')
                ),
                verify_accuraterip=s.get_boolean('verify-accuraterip'),
                drive_sample_offset=s.get_int('drive-sample-offset'),
                drive_offset_configured=s.get_boolean('drive-offset-configured'),
                drive_offset_device=s.get_string('drive-offset-device'),
                burst_fallback=s.get_boolean('burst-fallback'),
                write_rip_log=s.get_boolean('write-rip-log'),
                write_cue_file=_get_bool(s, 'write-cue-file', True),
                test_and_copy=_get_bool(s, 'test-and-copy', True),
                defeat_audio_cache=_get_bool(s, 'defeat-audio-cache', True),
                drive_cache_configured=_get_bool(s, 'drive-cache-configured', False),
                drive_caches_audio=_get_bool(s, 'drive-caches-audio', False),
                drive_cache_message=_get_string(s, 'drive-cache-message', ''),
                drive_accurate_stream_configured=_get_bool(
                    s, 'drive-accurate-stream-configured', False
                ),
                drive_accurate_stream=_get_bool(s, 'drive-accurate-stream', False),
                drive_accurate_stream_message=_get_string(
                    s, 'drive-accurate-stream-message', ''
                ),
                rip_htoa=_get_bool(s, 'rip-htoa', True),
                rip_data_tracks=_get_bool(s, 'rip-data-tracks', True),
                copy_image=_get_bool(s, 'copy-image', False),
                auto_rip=_get_bool(s, 'auto-rip', False),
                auto_eject=_get_bool(s, 'auto-eject', False),
            )

        if not snap.output_directory.strip():
            snap.output_directory = default_output_directory()

        # Persist album-folder default if the stored template was the old one.
        if s is not None:
            stored_folder = (s.get_string('album-folder-template') or '').strip()
            if snap.album_folder_template != stored_folder:
                s.set_string('album-folder-template', snap.album_folder_template)

        # Migrate removed artwork size presets (e.g. 720 → 800).
        allowed = {px for px, _label in ARTWORK_SIZES}
        if snap.artwork_max_size in _ARTWORK_SIZE_ALIASES:
            snap.artwork_max_size = _ARTWORK_SIZE_ALIASES[snap.artwork_max_size]
        elif snap.artwork_max_size not in allowed:
            # Drop free-form custom sizes; keep default embed size.
            snap.artwork_max_size = 600
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
            'extra_encode_targets': ('extra-encode-targets', 'strv'),
            'flac_compression': ('flac-compression', 'int'),
            'mp3_quality': ('mp3-quality', 'string'),
            'aac_quality': ('aac-quality', 'string'),
            'aac_bitrate': ('aac-bitrate', 'int'),
            'opus_bitrate': ('opus-bitrate', 'int'),
            'apply_replaygain': ('apply-replaygain', 'bool'),
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
            'verify_accuraterip': ('verify-accuraterip', 'bool'),
            'drive_sample_offset': ('drive-sample-offset', 'int'),
            'drive_offset_configured': ('drive-offset-configured', 'bool'),
            'drive_offset_device': ('drive-offset-device', 'string'),
            'burst_fallback': ('burst-fallback', 'bool'),
            'write_rip_log': ('write-rip-log', 'bool'),
            'write_cue_file': ('write-cue-file', 'bool'),
            'test_and_copy': ('test-and-copy', 'bool'),
            'defeat_audio_cache': ('defeat-audio-cache', 'bool'),
            'drive_cache_configured': ('drive-cache-configured', 'bool'),
            'drive_caches_audio': ('drive-caches-audio', 'bool'),
            'drive_cache_message': ('drive-cache-message', 'string'),
            'drive_accurate_stream_configured': (
                'drive-accurate-stream-configured',
                'bool',
            ),
            'drive_accurate_stream': ('drive-accurate-stream', 'bool'),
            'drive_accurate_stream_message': (
                'drive-accurate-stream-message',
                'string',
            ),
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
                s.set_string(gkey, str(value))
            elif kind == 'int':
                s.set_int(gkey, int(value))
            elif kind == 'bool':
                s.set_boolean(gkey, bool(value))
            elif kind == 'strv':
                if attr == 'extra_encode_targets':
                    specs = [
                        encode_target_to_spec(t)
                        for t in (value or ())
                    ]
                    s.set_strv(gkey, specs)
                else:
                    s.set_strv(gkey, list(value or []))
        return current
