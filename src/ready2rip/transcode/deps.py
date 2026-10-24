# SPDX-License-Identifier: GPL-3.0-or-later
"""Discover Melton scripts and system dependencies."""

from __future__ import annotations

import os
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
VENDORED_DIR = REPO_ROOT / "vendor" / "video_transcoding"

SCRIPT_NAMES = {
    "transcode": "transcode-video.rb",
    "detect_crop": "detect-crop.rb",
    "convert": "convert-video.rb",
}


@dataclass(frozen=True)
class DepStatus:
    name: str
    ok: bool
    path: str | None
    detail: str = ""


def _which(name: str) -> str | None:
    return shutil.which(name)


def find_script(tool: str) -> Path | None:
    """Resolve a Melton script path (vendored first, then PATH)."""
    filename = SCRIPT_NAMES.get(tool)
    if not filename:
        return None

    env_key = f"READY2RIP_MELTON_{tool.upper()}_SCRIPT"
    env_path = os.environ.get(env_key)
    if env_path:
        p = Path(env_path).expanduser()
        if p.is_file():
            return p

    vendored = VENDORED_DIR / filename
    if vendored.is_file():
        return vendored

    on_path = _which(filename) or _which(filename.removesuffix(".rb"))
    if on_path:
        return Path(on_path)
    return None


def check_dependencies() -> list[DepStatus]:
    """Return status for Melton toolchain pieces on this machine."""
    statuses: list[DepStatus] = []

    ruby = _which("ruby")
    statuses.append(
        DepStatus(
            "ruby",
            ruby is not None,
            ruby,
            "Ruby runtime for Melton scripts",
        )
    )

    for tool, label in (
        ("HandBrakeCLI", "HandBrake command-line (required for transcode/detect-crop)"),
        ("ffprobe", "FFmpeg probe (required)"),
        ("ffmpeg", "FFmpeg (required for convert-video)"),
    ):
        path = _which(tool)
        statuses.append(DepStatus(tool, path is not None, path, label))

    for key, label in SCRIPT_NAMES.items():
        path = find_script(key)
        statuses.append(
            DepStatus(
                label,
                path is not None,
                str(path) if path else None,
                f"Melton {key.replace('_', '-')} tool",
            )
        )

    # Optional (older Melton gem era); not required by 2025 scripts.
    mkv = _which("mkvpropedit")
    statuses.append(
        DepStatus(
            "mkvpropedit",
            mkv is not None,
            mkv,
            "Optional (legacy Melton gem); not required by 2025 scripts",
        )
    )
    return statuses


def dependencies_ok_for(tool: str) -> tuple[bool, list[DepStatus]]:
    statuses = check_dependencies()
    by_name = {s.name: s for s in statuses}
    required = ["ruby", "ffprobe"]
    if tool == "transcode":
        required += ["HandBrakeCLI", SCRIPT_NAMES["transcode"]]
    elif tool == "detect_crop":
        required += ["HandBrakeCLI", SCRIPT_NAMES["detect_crop"]]
    elif tool == "convert":
        required += ["ffmpeg", SCRIPT_NAMES["convert"]]
    else:
        return False, statuses
    ok = all(by_name.get(name, DepStatus(name, False, None)).ok for name in required)
    return ok, statuses


def dependencies_as_dicts() -> list[dict]:
    return [asdict(s) for s in check_dependencies()]
