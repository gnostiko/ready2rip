# Melton transcoding GUI

Local web UI for Lisa Melton’s [`video_transcoding`](https://github.com/lisamelton/video_transcoding)
scripts, shipped inside ready2rip.

## Quick start

```bash
./run-transcode --check   # dependency report
./run-transcode           # http://127.0.0.1:8765/
```

## Design notes

- Stdlib-only Python HTTP server (`http.server`) — no Flask/Django.
- Argv builder only emits flags documented by Melton’s `--help`.
- Scripts are vendored under `vendor/video_transcoding/` (MIT, pinned 2025.01.28).
- The CD-ripper GTK app is unchanged; this is a sibling entry point.

## Fedora verification (this machine)

| Dependency | Status |
|------------|--------|
| Ruby | OK |
| HandBrakeCLI (`HandBrake-cli`) | OK |
| ffprobe / ffmpeg | OK |
| Melton scripts (vendored) | OK |
| mkvpropedit | Missing (optional; not required by 2025 scripts) |

Do **not** install the old `video_transcoding` RubyGem — Melton’s README warns
that packaging is obsolete; use the standalone `.rb` scripts.
