# SPDX-License-Identifier: GPL-3.0-or-later
"""Application settings (GSettings with in-memory fallback)."""

from __future__ import annotations

from dataclasses import dataclass, fields, replace
from pathlib import Path

from gi.repository import Gio, GLib


SCHEMA_ID = 'org.ready2rip.Ready2Rip'

ENCODERS = (
    ('flac', 'FLAC (lossless)'),
    ('mp3', 'MP3'),
    ('opus', 'Opus'),
    ('wav', 'WAV (uncompressed)'),
)

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


def _get_bool(settings: Gio.Settings, key: str, default: bool) -> bool:
    """Read a boolean key; fall back if the installed schema is older."""
    try:
        return settings.get_boolean(key)
    except Exception:  # noqa: BLE001
        return default


def _get_string(settings: Gio.Settings, key: str, default: str) -> str:
    try:
        return settings.get_string(key) or default
    except Exception:  # noqa: BLE001
        return default


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
    output_directory: str = ''  # empty → default_output_directory() (Music)
    encode_format: str = 'flac'
    flac_compression: int = 5
    mp3_bitrate: int = 320
    opus_bitrate: int = 160
    apply_replaygain: bool = True
    embed_artwork: bool = True
    fetch_artwork: bool = True
    artwork_source_itunes: bool = True
    artwork_source_caa: bool = True
    artwork_source_deezer: bool = True
    artwork_max_size: int = 600
    use_musicbrainz: bool = True
    use_freedb: bool = True
    auto_lookup_metadata: bool = True
    filename_template: str = '{track:02d} - {title}'
    # {disc_folder} expands to CD1, CD2, … only when total discs > 1.
    album_folder_template: str = '{album_artist}/{album}/{disc_folder}'
    verify_accuraterip: bool = True
    drive_sample_offset: int = 0
    drive_offset_configured: bool = False
    drive_offset_device: str = ''
    burst_fallback: bool = True
    write_rip_log: bool = True
    # EAC recommendation for secure/accurate rips: write a multi-file .cue sheet.
    write_cue_file: bool = True
    test_and_copy: bool = True
    defeat_audio_cache: bool = True
    drive_cache_configured: bool = False
    drive_caches_audio: bool = False
    drive_cache_message: str = ''
    drive_accurate_stream_configured: bool = False
    drive_accurate_stream: bool = False
    drive_accurate_stream_message: str = ''
    drive_c2_configured: bool = False
    drive_c2_pointers: bool = False
    drive_c2_message: str = ''
    rip_htoa: bool = True
    # EAC "Copy Image" mode (CUE is separate: write_cue_file)
    copy_image: bool = False
    auto_rip: bool = False
    auto_eject: bool = False

    def resolved_output_directory(self) -> Path:
        """Output folder for rips; empty setting means the Music directory."""
        raw = self.output_directory.strip()
        if raw:
            return Path(raw).expanduser()
        return default_music_directory()


class SettingsStore:
    """Read/write GSettings when the schema is available; else memory defaults."""

    def __init__(self) -> None:
        self._settings = self._open_settings()
        self._memory = AppSettings()

    @staticmethod
    def _open_settings() -> Gio.Settings | None:
        source = Gio.SettingsSchemaSource.get_default()
        if source is None:
            return None
        schema = source.lookup(SCHEMA_ID, True)
        if schema is None:
            return None
        return Gio.Settings.new_full(schema, None, None)

    @property
    def gsettings(self) -> Gio.Settings | None:
        return self._settings

    def get(self) -> AppSettings:
        s = self._settings
        if s is None:
            snap = replace(self._memory)
        else:
            snap = AppSettings(
                device=s.get_string('device') or '/dev/sr0',
                output_directory=s.get_string('output-directory') or '',
                encode_format=s.get_string('encode-format') or 'flac',
                flac_compression=s.get_int('flac-compression'),
                mp3_bitrate=s.get_int('mp3-bitrate'),
                opus_bitrate=s.get_int('opus-bitrate'),
                apply_replaygain=s.get_boolean('apply-replaygain'),
                embed_artwork=s.get_boolean('embed-artwork'),
                fetch_artwork=s.get_boolean('fetch-artwork'),
                artwork_source_itunes=_get_bool(s, 'artwork-source-itunes', True),
                artwork_source_caa=_get_bool(s, 'artwork-source-caa', True),
                artwork_source_deezer=_get_bool(s, 'artwork-source-deezer', True),
                artwork_max_size=s.get_int('artwork-max-size'),
                use_musicbrainz=s.get_boolean('use-musicbrainz'),
                use_freedb=s.get_boolean('use-freedb'),
                auto_lookup_metadata=s.get_boolean('auto-lookup-metadata'),
                filename_template=s.get_string('filename-template')
                or '{track:02d} - {title}',
                album_folder_template=s.get_string('album-folder-template')
                or '{album_artist}/{album}/{disc_folder}',
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
                drive_c2_configured=_get_bool(s, 'drive-c2-configured', False),
                drive_c2_pointers=_get_bool(s, 'drive-c2-pointers', False),
                drive_c2_message=_get_string(s, 'drive-c2-message', ''),
                rip_htoa=_get_bool(s, 'rip-htoa', True),
                copy_image=_get_bool(s, 'copy-image', False),
                auto_rip=_get_bool(s, 'auto-rip', False),
                auto_eject=_get_bool(s, 'auto-eject', False),
            )

        if not snap.output_directory.strip():
            snap.output_directory = default_output_directory()

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
            'output_directory': ('output-directory', 'string'),
            'encode_format': ('encode-format', 'string'),
            'flac_compression': ('flac-compression', 'int'),
            'mp3_bitrate': ('mp3-bitrate', 'int'),
            'opus_bitrate': ('opus-bitrate', 'int'),
            'apply_replaygain': ('apply-replaygain', 'bool'),
            'embed_artwork': ('embed-artwork', 'bool'),
            'fetch_artwork': ('fetch-artwork', 'bool'),
            'artwork_source_itunes': ('artwork-source-itunes', 'bool'),
            'artwork_source_caa': ('artwork-source-caa', 'bool'),
            'artwork_source_deezer': ('artwork-source-deezer', 'bool'),
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
            'drive_c2_configured': ('drive-c2-configured', 'bool'),
            'drive_c2_pointers': ('drive-c2-pointers', 'bool'),
            'drive_c2_message': ('drive-c2-message', 'string'),
            'rip_htoa': ('rip-htoa', 'bool'),
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
                s.set_string(gkey, str(value))
            elif kind == 'int':
                s.set_int(gkey, int(value))
            elif kind == 'bool':
                s.set_boolean(gkey, bool(value))
        return current
