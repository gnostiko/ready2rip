# SPDX-License-Identifier: GPL-3.0-or-later
"""Launch the Melton transcode local web UI."""

from __future__ import annotations

import argparse
import sys
import webbrowser

from ready2rip.transcode.deps import check_dependencies
from ready2rip.transcode.server import serve


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="ready2rip Melton video_transcoding GUI (local web UI)",
    )
    parser.add_argument("--host", default="127.0.0.1", help="bind address")
    parser.add_argument("--port", type=int, default=8765, help="bind port")
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="do not open a browser tab",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="print dependency status and exit",
    )
    args = parser.parse_args(argv)

    statuses = check_dependencies()
    if args.check:
        width = max(len(s.name) for s in statuses)
        for s in statuses:
            mark = "OK" if s.ok else "MISSING"
            path = s.path or "—"
            print(f"{s.name:<{width}}  {mark:<7}  {path}")
            if s.detail:
                print(f"{'':<{width}}  {s.detail}")
        return 0 if all(
            s.ok
            for s in statuses
            if s.name
            in (
                "ruby",
                "HandBrakeCLI",
                "ffprobe",
                "ffmpeg",
                "transcode-video.rb",
                "detect-crop.rb",
                "convert-video.rb",
            )
        ) else 1

    missing = [s.name for s in statuses if not s.ok and s.name != "mkvpropedit"]
    if missing:
        print("Warning: missing tools:", ", ".join(missing), file=sys.stderr)
        print("Run with --check for details.", file=sys.stderr)

    httpd = serve(args.host, args.port)
    url = f"http://{args.host}:{args.port}/"
    print(f"Melton transcode GUI at {url}")
    print("Ctrl+C to stop.")
    if not args.no_open:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
