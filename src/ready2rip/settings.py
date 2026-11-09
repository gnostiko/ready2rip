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
from ready2rip.rip.encode_dest import normalize_encode_format
from ready2rip.settings_defs import (
    AAC_QUALITIES,
    AAC_QUALITY_IDS,
    ALSO_ENCODER_IDS,
    ALSO_ENCODERS,
    ALSO_FOLDER_LAYOUT_IDS,
    ALSO_FOLDER_LAYOUTS,
    ARTWORK_SIZE_IDS,
    ARTWORK_SIZES,
    DEFAULT_ARTWORK_MAX_SIZE,
    DEFAULT_LYRICS_SAVE_MODE,
    ENCODER_IDS,
    ENCODERS,
    FLAC_LEVELS,
    LYRICS_SAVE_MODE_IDS,
    LYRICS_SAVE_MODES,
    MP3_QUALITIES,
    MP3_QUALITY_IDS,
    OPUS_BITRATES,
    QAAC_QUALITIES,
    QAAC_QUALITY_IDS,
    aac_quality_from_bitrate,
    lame_args_for_mp3_quality,
    lyrics_mode_writes_sidecar,
    lyrics_mode_writes_tags,
    mp3_quality_from_bitrate,
    normalize_aac_quality,
    normalize_also_encode_folder,
    normalize_also_encode_format,
    normalize_artwork_max_size,
    normalize_lyrics_save_mode,
    normalize_mp3_quality,
    normalize_qaac_quality,
    opus_bitrate_label,
    rip_log_aac_quality,
    rip_log_mp3_quality,
    rip_log_qaac_quality,
)
from ready2rip.tags.artists import (
    DEFAULT_ARTIST_STYLE,
    normalize_artist_style,
)

# Re-export defs for callers that import from ready2rip.settings.
__all__ = [
    'AAC_QUALITIES',
    'AAC_QUALITY_IDS',
    'ALSO_ENCODERS',
    'ALSO_ENCODER_IDS',
    'ALSO_FOLDER_LAYOUTS',
    'ALSO_FOLDER_LAYOUT_IDS',
    'ARTWORK_SIZES',
    'ARTWORK_SIZE_IDS',
    'DEFAULT_ARTWORK_MAX_SIZE',
    'DEFAULT_LYRICS_SAVE_MODE',
    'ENCODERS',
    'ENCODER_IDS',
    'FLAC_LEVELS',
    'LYRICS_SAVE_MODES',
    'LYRICS_SAVE_MODE_IDS',
    'MP3_QUALITIES',
    'MP3_QUALITY_IDS',
    'OPUS_BITRATES',
    'QAAC_QUALITIES',
    'QAAC_QUALITY_IDS',
    'SCHEMA_ID',
    'AppSettings',
    'SettingsStore',
    'aac_quality_from_bitrate',
    'default_music_directory',
    'default_output_directory',
    'lame_args_for_mp3_quality',
    'lyrics_mode_writes_sidecar',
    'lyrics_mode_writes_tags',
    'mp3_quality_from_bitrate',
    'normalize_aac_quality',
    'normalize_also_encode_folder',
    'normalize_also_encode_format',
    'normalize_artwork_max_size',
    'normalize_encode_format',
    'normalize_lyrics_save_mode',
    'normalize_mp3_quality',
    'normalize_qaac_quality',
    'opus_bitrate_label',
    'rip_log_aac_quality',
    'rip_log_mp3_quality',
    'rip_log_qaac_quality',
]

log = logging.getLogger(__name__)

SCHEMA_ID = 'org.ready2rip.Ready2Rip'
_SCHEMA_XML = 'org.ready2rip.Ready2Rip.gschema.xml'


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


def _load_artwork_max_size_lossless(settings: Gio.Settings) -> int:
    """Lossless embed size; seed once from legacy shared size, then stay independent."""
    try:
        user = settings.get_user_value('artwork-max-size-lossless')
        if user is not None:
            return normalize_artwork_max_size(int(user.get_int32()))
    except Exception:
        pass
    # First run after the split: pin a copy of the old shared size so later
    # lossy changes do not drag lossless along.
    seeded = normalize_artwork_max_size(
        _get_int(settings, 'artwork-max-size', DEFAULT_ARTWORK_MAX_SIZE)
    )
    try:
        settings.set_int('artwork-max-size-lossless', int(seeded))
    except Exception:
        pass
    return seeded


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
    # qaac (Apple AAC / Wine) — popular TVBR / CVBR / CBR presets.
    qaac_quality: str = 'cvbr256'
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
    # Write ready2rip + version into COMMENT / ©cmt (Ripper tag).
    # Encoded by / ENCODER / ©too always uses the codec tool + version.
    add_ripper_tag: bool = True
    # Fetch lyrics from LRCLIB after a successful album match.
    fetch_lyrics: bool = False
    # Save lyrics with lossless rips (master switch).
    embed_lyrics: bool = True
    # How to store them: both | embed | sidecar.
    lyrics_save_mode: str = DEFAULT_LYRICS_SAVE_MODE
    # Embed cover into MP3 / M4A / Opus.
    embed_artwork: bool = True
    # Embed cover into FLAC / WAV / AIFF (off by default; folder cover still written).
    embed_artwork_lossless: bool = False
    fetch_artwork: bool = True
    artwork_source_itunes: bool = True
    artwork_source_caa: bool = True
    # Longest edge for embeds into lossy files (MP3 / M4A / Opus).
    artwork_max_size: int = DEFAULT_ARTWORK_MAX_SIZE
    # Longest edge for embeds into lossless files (FLAC / WAV / AIFF).
    artwork_max_size_lossless: int = DEFAULT_ARTWORK_MAX_SIZE
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

    def wants_embed_artwork(self) -> bool:
        """True when lossy and/or lossless file embeds are enabled."""
        return bool(self.embed_artwork or self.embed_artwork_lossless)

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
            return Gio.SettingsSchemaSource.new_from_directory(str(cache), default, True)
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
                encode_format=normalize_encode_format(_get_string(s, 'encode-format', 'flac')),
                flac_compression=s.get_int('flac-compression'),
                mp3_quality=_load_mp3_quality(s),
                aac_quality=(aq := _load_aac_quality(s)),
                aac_bitrate=(
                    int(aq[3:])
                    if aq.startswith('cbr') and aq[3:].isdigit()
                    else _get_int(s, 'aac-bitrate', 256)
                ),
                qaac_quality=normalize_qaac_quality(_get_string(s, 'qaac-quality', 'cvbr256')),
                opus_bitrate=s.get_int('opus-bitrate'),
                also_encode=_get_bool(s, 'also-encode', False),
                also_encode_format=normalize_also_encode_format(
                    _get_string(s, 'also-encode-format', 'mp3')
                ),
                also_encode_folder=normalize_also_encode_folder(
                    _get_string(s, 'also-encode-folder', 'format_subdir')
                ),
                also_encode_directory=_get_string(s, 'also-encode-directory', ''),
                apply_replaygain=s.get_boolean('apply-replaygain'),
                write_sound_check=_get_bool(s, 'write-sound-check', True),
                add_ripper_tag=_get_bool(s, 'add-ripper-tag', True),
                fetch_lyrics=_get_bool(s, 'fetch-lyrics', False),
                embed_lyrics=_get_bool(s, 'embed-lyrics', True),
                lyrics_save_mode=normalize_lyrics_save_mode(
                    _get_string(s, 'lyrics-save-mode', DEFAULT_LYRICS_SAVE_MODE)
                ),
                embed_artwork=s.get_boolean('embed-artwork'),
                embed_artwork_lossless=_get_bool(s, 'embed-artwork-lossless', False),
                fetch_artwork=s.get_boolean('fetch-artwork'),
                artwork_source_itunes=_get_bool(s, 'artwork-source-itunes', True),
                artwork_source_caa=_get_bool(s, 'artwork-source-caa', True),
                artwork_max_size=normalize_artwork_max_size(
                    _get_int(s, 'artwork-max-size', DEFAULT_ARTWORK_MAX_SIZE)
                ),
                artwork_max_size_lossless=_load_artwork_max_size_lossless(s),
                use_musicbrainz=s.get_boolean('use-musicbrainz'),
                use_freedb=s.get_boolean('use-freedb'),
                auto_lookup_metadata=s.get_boolean('auto-lookup-metadata'),
                filename_template=migrate_filename_template(s.get_string('filename-template')),
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

        # Persist migrated naming defaults / primary format when legacy.
        if s is not None:
            stored_folder = (s.get_string('album-folder-template') or '').strip()
            if snap.album_folder_template != stored_folder:
                s.set_string('album-folder-template', snap.album_folder_template)
            stored_name = (s.get_string('filename-template') or '').strip()
            if snap.filename_template != stored_name:
                s.set_string('filename-template', snap.filename_template)
            try:
                stored_fmt = (s.get_string('encode-format') or '').strip().lower()
            except Exception:
                stored_fmt = ''
            if snap.encode_format != stored_fmt:
                try:
                    s.set_string('encode-format', snap.encode_format)
                except Exception:
                    pass

        # Migrate removed / oversized artwork presets (e.g. 1000 → 800).
        prior_lossy = int(snap.artwork_max_size)
        prior_lossless = int(snap.artwork_max_size_lossless)
        snap.artwork_max_size = normalize_artwork_max_size(snap.artwork_max_size)
        snap.artwork_max_size_lossless = normalize_artwork_max_size(snap.artwork_max_size_lossless)
        if s is not None and snap.artwork_max_size != prior_lossy:
            try:
                s.set_int('artwork-max-size', int(snap.artwork_max_size))
            except Exception:
                pass
        if s is not None and snap.artwork_max_size_lossless != prior_lossless:
            try:
                s.set_int(
                    'artwork-max-size-lossless',
                    int(snap.artwork_max_size_lossless),
                )
            except Exception:
                pass
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
            'qaac_quality': ('qaac-quality', 'string'),
            'opus_bitrate': ('opus-bitrate', 'int'),
            'also_encode': ('also-encode', 'bool'),
            'also_encode_format': ('also-encode-format', 'string'),
            'also_encode_folder': ('also-encode-folder', 'string'),
            'also_encode_directory': ('also-encode-directory', 'string'),
            'apply_replaygain': ('apply-replaygain', 'bool'),
            'write_sound_check': ('write-sound-check', 'bool'),
            'add_ripper_tag': ('add-ripper-tag', 'bool'),
            'fetch_lyrics': ('fetch-lyrics', 'bool'),
            'embed_lyrics': ('embed-lyrics', 'bool'),
            'lyrics_save_mode': ('lyrics-save-mode', 'string'),
            'embed_artwork': ('embed-artwork', 'bool'),
            'embed_artwork_lossless': ('embed-artwork-lossless', 'bool'),
            'fetch_artwork': ('fetch-artwork', 'bool'),
            'artwork_source_itunes': ('artwork-source-itunes', 'bool'),
            'artwork_source_caa': ('artwork-source-caa', 'bool'),
            'artwork_max_size': ('artwork-max-size', 'int'),
            'artwork_max_size_lossless': ('artwork-max-size-lossless', 'int'),
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
                elif attr == 'qaac_quality':
                    value = normalize_qaac_quality(str(value))
                    current = replace(current, qaac_quality=value)
                elif attr == 'artist_style':
                    value = normalize_artist_style(str(value))
                    current = replace(current, artist_style=value)
                elif attr == 'encode_format':
                    value = normalize_encode_format(str(value))
                    current = replace(current, encode_format=value)
                elif attr == 'also_encode_format':
                    value = normalize_also_encode_format(str(value))
                    current = replace(current, also_encode_format=value)
                elif attr == 'also_encode_folder':
                    value = normalize_also_encode_folder(str(value))
                    current = replace(current, also_encode_folder=value)
                elif attr == 'lyrics_save_mode':
                    value = normalize_lyrics_save_mode(str(value))
                    current = replace(current, lyrics_save_mode=value)
                s.set_string(gkey, str(value))
            elif kind == 'int':
                s.set_int(gkey, int(value))
            elif kind == 'bool':
                s.set_boolean(gkey, bool(value))
        return current
