# SPDX-License-Identifier: GPL-3.0-or-later
"""Build Melton CLI argv from UI options (real flags only)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from ready2rip.transcode.deps import find_script

ToolName = Literal["transcode", "detect_crop", "convert"]

# From transcode-video.rb --help / OptionParser
VIDEO_MODES = ("h264", "hevc", "nvenc-hevc", "av1", "nvenc-av1", "none")
AUDIO_MODES = ("aac", "opus", "eac3", "none")
AAC_ENCODERS = ("av_aac", "fdk_aac", "ca_aac")
CROP_MODES = ("conservative", "auto")
# Melton accepts these via --extra format=… (see process_input)
CONTAINERS = (
    ("default", "Matroska .mkv (Melton default)"),
    ("av_mp4", "MP4 via -x format=av_mp4"),
    ("av_webm", "WebM via -x format=av_webm"),
    ("av_mkv", "MKV via -x format=av_mkv"),
)

_EXTRA_RE = re.compile(r"^([a-zA-Z][a-zA-Z0-9-]+)(?:=(.+))?$")
_TRACK_RE = re.compile(r"^[0-9]+$")
_LANG_RE = re.compile(r"^[a-z]{3}$")


@dataclass
class TranscodeOptions:
    tool: ToolName = "transcode"
    inputs: list[str] = field(default_factory=list)
    output_dir: str | None = None

    dry_run: bool = False
    debug: bool = False

    # transcode-video.rb
    mode: str = "h264"
    preset: str | None = None
    bitrate: int | None = None
    quality: float | None = None
    no_bframe_refs: bool = False
    audio_mode: str = "default"  # default = Melton (aac, or opus for av1 modes)
    add_audio: list[str] = field(default_factory=list)  # empty → Melton default track 1
    ac3_surround: bool = False
    aac_encoder: str = "av_aac"
    burn_subtitle: str = "auto"  # auto | none | track number
    add_subtitle: list[str] = field(default_factory=list)
    container: str = "default"
    extra: list[str] = field(default_factory=list)

    # detect-crop.rb
    crop_mode: str = "conservative"


class OptionError(ValueError):
    pass


def options_from_dict(data: dict[str, Any]) -> TranscodeOptions:
    tool = data.get("tool") or "transcode"
    if tool not in ("transcode", "detect_crop", "convert"):
        raise OptionError(f"unsupported tool: {tool}")

    inputs = data.get("inputs") or []
    if isinstance(inputs, str):
        inputs = [inputs]
    inputs = [str(p).strip() for p in inputs if str(p).strip()]
    if not inputs:
        raise OptionError("at least one input file is required")

    def _list(key: str) -> list[str]:
        raw = data.get(key) or []
        if isinstance(raw, str):
            raw = [raw] if raw.strip() else []
        return [str(x).strip() for x in raw if str(x).strip()]

    bitrate = data.get("bitrate")
    quality = data.get("quality")
    try:
        bitrate_i = int(bitrate) if bitrate not in (None, "",) else None
    except (TypeError, ValueError) as exc:
        raise OptionError("bitrate must be an integer") from exc
    try:
        quality_f = float(quality) if quality not in (None, "",) else None
    except (TypeError, ValueError) as exc:
        raise OptionError("quality must be a number") from exc

    return TranscodeOptions(
        tool=tool,  # type: ignore[arg-type]
        inputs=inputs,
        output_dir=(str(data["output_dir"]).strip() or None)
        if data.get("output_dir")
        else None,
        dry_run=bool(data.get("dry_run")),
        debug=bool(data.get("debug")),
        mode=str(data.get("mode") or "h264"),
        preset=(str(data["preset"]).strip() or None) if data.get("preset") else None,
        bitrate=bitrate_i,
        quality=quality_f,
        no_bframe_refs=bool(data.get("no_bframe_refs")),
        audio_mode=str(data.get("audio_mode") or "default"),
        add_audio=_list("add_audio"),
        ac3_surround=bool(data.get("ac3_surround")),
        aac_encoder=str(data.get("aac_encoder") or "av_aac"),
        burn_subtitle=str(data.get("burn_subtitle") or "auto"),
        add_subtitle=_list("add_subtitle"),
        container=str(data.get("container") or "default"),
        extra=_list("extra"),
        crop_mode=str(data.get("crop_mode") or "conservative"),
    )


def build_argv(opts: TranscodeOptions) -> tuple[list[str], Path | None]:
    """Return (argv, cwd). cwd is output_dir for tools that write beside cwd."""
    script = find_script(opts.tool)
    if script is None:
        raise OptionError(f"Melton script not found for tool: {opts.tool}")

    ruby = "ruby"
    argv: list[str] = [ruby, str(script)]

    if opts.tool == "detect_crop":
        if opts.crop_mode not in CROP_MODES:
            raise OptionError(f"unsupported crop mode: {opts.crop_mode}")
        argv += ["--mode", opts.crop_mode]
        argv += _validated_inputs(opts.inputs)
        return argv, None

    if opts.tool == "convert":
        if opts.debug:
            argv.append("--debug")
        if opts.dry_run:
            argv.append("--dry-run")
        argv += _validated_inputs(opts.inputs)
        return argv, _resolve_output_dir(opts.output_dir)

    # transcode
    if opts.debug:
        argv.append("--debug")
    if opts.dry_run:
        argv.append("--dry-run")

    if opts.mode not in VIDEO_MODES:
        raise OptionError(f"unsupported video mode: {opts.mode}")
    if opts.mode != "h264":
        argv += ["--mode", opts.mode]

    if opts.preset:
        argv += ["--preset", opts.preset]

    if opts.bitrate is not None and opts.quality is not None:
        raise OptionError("set bitrate or quality, not both")
    if opts.bitrate is not None:
        argv += ["--bitrate", str(opts.bitrate)]
    if opts.quality is not None:
        argv += ["--quality", str(opts.quality)]

    if opts.no_bframe_refs:
        argv.append("--no-bframe-refs")

    if opts.audio_mode != "default":
        if opts.audio_mode not in AUDIO_MODES:
            raise OptionError(f"unsupported audio mode: {opts.audio_mode}")
        argv += ["--audio-mode", opts.audio_mode]

    for sel in opts.add_audio:
        _validate_track_or_lang(sel, "add-audio")
        argv += ["--add-audio", sel]

    if opts.ac3_surround:
        argv.append("--ac3-surround")

    if opts.aac_encoder != "av_aac":
        if opts.aac_encoder not in AAC_ENCODERS:
            raise OptionError(f"invalid AAC encoder: {opts.aac_encoder}")
        argv += ["--aac-encoder", opts.aac_encoder]

    # Subtitles: Melton default is automatic burn; --add-subtitle disables burning.
    if opts.add_subtitle:
        for sel in opts.add_subtitle:
            _validate_track_or_lang(sel, "add-subtitle")
            argv += ["--add-subtitle", sel]
    elif opts.burn_subtitle != "auto":
        if opts.burn_subtitle == "none":
            argv += ["--burn-subtitle", "none"]
        elif _TRACK_RE.match(opts.burn_subtitle):
            argv += ["--burn-subtitle", opts.burn_subtitle]
        else:
            raise OptionError("burn-subtitle must be auto, none, or a track number")

    extras = list(opts.extra)
    if opts.container and opts.container != "default":
        allowed = {c for c, _ in CONTAINERS if c != "default"}
        if opts.container not in allowed:
            raise OptionError(f"unsupported container: {opts.container}")
        extras.append(f"format={opts.container}")

    for item in extras:
        if not _EXTRA_RE.match(item):
            raise OptionError(f"invalid --extra value: {item}")
        argv += ["--extra", item]

    argv += _validated_inputs(opts.inputs)
    return argv, _resolve_output_dir(opts.output_dir)


def catalog() -> dict[str, Any]:
    """Static option catalog for the UI (Melton defaults annotated)."""
    return {
        "tools": [
            {"id": "transcode", "label": "transcode-video.rb", "default": True},
            {"id": "detect_crop", "label": "detect-crop.rb"},
            {"id": "convert", "label": "convert-video.rb"},
        ],
        "video_modes": [
            {"id": "h264", "label": "h264 (default) — x264 two-pass CBR", "default": True},
            {"id": "hevc", "label": "hevc — x265_10bit CQ (slow, 4K HDR)"},
            {"id": "nvenc-hevc", "label": "nvenc-hevc — Nvidia HEVC 10-bit"},
            {"id": "av1", "label": "av1 — svt_av1_10bit (audio defaults to opus)"},
            {"id": "nvenc-av1", "label": "nvenc-av1 — Nvidia AV1 (audio defaults to opus)"},
            {"id": "none", "label": "none — no video encode (HandBrake still invoked)"},
        ],
        "audio_modes": [
            {
                "id": "default",
                "label": "Melton default (aac; opus for av1 / nvenc-av1)",
                "default": True,
            },
            {"id": "aac", "label": "aac"},
            {"id": "opus", "label": "opus"},
            {"id": "eac3", "label": "eac3"},
            {"id": "none", "label": "none"},
        ],
        "aac_encoders": [
            {"id": "av_aac", "label": "av_aac (default)", "default": True},
            {"id": "fdk_aac", "label": "fdk_aac"},
            {"id": "ca_aac", "label": "ca_aac"},
        ],
        "burn_subtitle": [
            {"id": "auto", "label": "automatic (default — forced track)", "default": True},
            {"id": "none", "label": "none"},
        ],
        "containers": [{"id": c, "label": l, "default": c == "default"} for c, l in CONTAINERS],
        "crop_modes": [
            {"id": "conservative", "label": "conservative (default)", "default": True},
            {"id": "auto", "label": "auto"},
        ],
        "defaults_note": (
            "Melton defaults: MKV, h264/x264 two-pass, first audio track, "
            "forced subtitles burned/included, conservative crop, "
            "AAC audio (Opus for AV1 modes)."
        ),
    }


def _validated_inputs(inputs: list[str]) -> list[str]:
    out: list[str] = []
    for raw in inputs:
        path = Path(raw).expanduser().resolve()
        if not path.exists():
            raise OptionError(f"input not found: {path}")
        out.append(str(path))
    return out


def _resolve_output_dir(output_dir: str | None) -> Path | None:
    if not output_dir:
        return None
    path = Path(output_dir).expanduser().resolve()
    if not path.is_dir():
        raise OptionError(f"output directory not found: {path}")
    return path


def _validate_track_or_lang(value: str, flag: str) -> None:
    if value == "all" or _TRACK_RE.match(value) or _LANG_RE.match(value):
        return
    # Melton also accepts arbitrary title STRING match
    if value:
        return
    raise OptionError(f"empty {flag} selection")
