# SPDX-License-Identifier: GPL-3.0-or-later
"""Probe media streams with ffprobe for UI dropdowns."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any


COMMON_LANGS = [
    ("", "— (use track number / Melton default)"),
    ("all", "all languages"),
    ("eng", "English (eng)"),
    ("jpn", "Japanese (jpn)"),
    ("spa", "Spanish (spa)"),
    ("fra", "French (fra)"),
    ("deu", "German (deu)"),
    ("ita", "Italian (ita)"),
    ("por", "Portuguese (por)"),
    ("rus", "Russian (rus)"),
    ("chi", "Chinese (chi)"),
    ("kor", "Korean (kor)"),
    ("hin", "Hindi (hin)"),
    ("und", "Undefined (und)"),
]


def probe_media(path: str | Path) -> dict[str, Any]:
    """Return condensed stream info for the UI."""
    media = Path(path).expanduser().resolve()
    if not media.is_file():
        raise FileNotFoundError(f"not a file: {media}")

    cmd = [
        "ffprobe",
        "-loglevel",
        "quiet",
        "-show_streams",
        "-show_format",
        "-print_format",
        "json",
        str(media),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"ffprobe failed ({proc.returncode})")

    raw = json.loads(proc.stdout)
    audio: list[dict[str, Any]] = []
    subs: list[dict[str, Any]] = []
    video: list[dict[str, Any]] = []
    a_i = s_i = v_i = 0

    for stream in raw.get("streams") or []:
        codec_type = stream.get("codec_type")
        tags = stream.get("tags") or {}
        disposition = stream.get("disposition") or {}
        base = {
            "codec": stream.get("codec_name") or "",
            "language": tags.get("language") or "",
            "title": tags.get("title") or "",
            "forced": bool(disposition.get("forced")),
            "default": bool(disposition.get("default")),
        }
        if codec_type == "audio":
            a_i += 1
            audio.append(
                {
                    **base,
                    "index": a_i,
                    "channels": stream.get("channels"),
                    "channel_layout": stream.get("channel_layout") or "",
                    "label": _audio_label(a_i, base, stream),
                }
            )
        elif codec_type == "subtitle":
            s_i += 1
            subs.append(
                {
                    **base,
                    "index": s_i,
                    "label": _sub_label(s_i, base),
                }
            )
        elif codec_type == "video":
            v_i += 1
            video.append(
                {
                    **base,
                    "index": v_i,
                    "width": stream.get("width"),
                    "height": stream.get("height"),
                    "label": (
                        f"Video {v_i}: {base['codec']} "
                        f"{stream.get('width')}×{stream.get('height')}"
                    ),
                }
            )

    fmt = raw.get("format") or {}
    return {
        "path": str(media),
        "filename": media.name,
        "duration": float(fmt.get("duration") or 0) or None,
        "format_name": fmt.get("format_name") or "",
        "size": int(fmt.get("size") or 0) or None,
        "video": video,
        "audio": audio,
        "subtitles": subs,
        "languages": [{"code": c, "label": l} for c, l in COMMON_LANGS],
    }


def _audio_label(index: int, base: dict[str, Any], stream: dict[str, Any]) -> str:
    bits = [f"#{index}"]
    if base["language"]:
        bits.append(base["language"])
    if base["title"]:
        bits.append(base["title"])
    bits.append(base["codec"] or "?")
    ch = stream.get("channels")
    if ch:
        bits.append(f"{ch}ch")
    return " · ".join(str(b) for b in bits)


def _sub_label(index: int, base: dict[str, Any]) -> str:
    bits = [f"#{index}"]
    if base["language"]:
        bits.append(base["language"])
    if base["title"]:
        bits.append(base["title"])
    bits.append(base["codec"] or "?")
    if base["forced"]:
        bits.append("forced")
    return " · ".join(str(b) for b in bits)
