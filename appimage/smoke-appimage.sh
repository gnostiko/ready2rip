#!/usr/bin/env bash
# Smoke-test a ready2rip AppImage the way AppImageHub roughly does:
# Ubuntu 22.04-era host, no network, Xvfb, must stay alive briefly without
# the DEFAULT_FLAGS / typelib crash (and preferably open a window).
#
# Usage:
#   ./appimage/smoke-appimage.sh dist/ready2rip-VERSION-x86_64.AppImage
set -euo pipefail

APPIMAGE="${1:-}"
if [ -z "$APPIMAGE" ] || [ ! -f "$APPIMAGE" ]; then
  echo "usage: $0 path/to/ready2rip-*.AppImage" >&2
  exit 2
fi
APPIMAGE="$(readlink -f "$APPIMAGE")"

need() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "error: missing tool for smoke test: $1" >&2
    exit 1
  }
}
need xvfb-run

WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/ready2rip-smoke.XXXXXX")"
cleanup() {
  if [ -n "${APID:-}" ]; then
    kill "$APID" 2>/dev/null || true
    wait "$APID" 2>/dev/null || true
  fi
  rm -rf "$WORKDIR"
}
trap cleanup EXIT

cp -f "$APPIMAGE" "$WORKDIR/app.AppImage"
chmod +x "$WORKDIR/app.AppImage"
LOG="$WORKDIR/run.log"

echo "==> smoke-test $(basename "$APPIMAGE") (Xvfb, no network)"

# Prefer extract-and-run so FUSE is not required on CI runners.
export APPIMAGE_EXTRACT_AND_RUN=1
export HOME="$WORKDIR/home"
mkdir -p "$HOME/.local/share/appimagekit"
touch "$HOME/.local/share/appimagekit/no_desktopintegration"

# Block outbound network for the app process tree when unshare is available
# (AppImageHub uses firejail --net=none).
RUNNER=(env HOME="$HOME" APPIMAGE_EXTRACT_AND_RUN=1)
if command -v unshare >/dev/null 2>&1 && unshare -n true 2>/dev/null; then
  RUNNER=(unshare -n -- "${RUNNER[@]}")
  echo "    network: unshare -n"
else
  echo "    network: host (unshare -n unavailable)"
fi

set +e
xvfb-run -a -s '-screen 0 1280x800x24' \
  timeout 25s "${RUNNER[@]}" "$WORKDIR/app.AppImage" \
  >"$LOG" 2>&1 &
APID=$!
# Give startup time; AppImageHub waits ~10s then looks for a window.
sleep 12
ALIVE=0
kill -0 "$APID" 2>/dev/null && ALIVE=1
set -e

if grep -E -q 'DEFAULT_FLAGS|AttributeError|Traceback \(most recent call last\)' "$LOG"; then
  echo "error: AppImage crashed during smoke test:" >&2
  tail -n 80 "$LOG" >&2
  exit 1
fi

if [ "$ALIVE" -ne 1 ]; then
  wait "$APID" 2>/dev/null || true
  echo "error: AppImage exited within 12s instead of staying up" >&2
  echo "---- log ----" >&2
  tail -n 80 "$LOG" >&2
  exit 1
fi

# Best-effort window check (not required if xdotool missing).
if command -v xdotool >/dev/null 2>&1; then
  if timeout 5 xdotool search --onlyvisible --name '.' >/dev/null 2>&1; then
    echo "    OK: visible window present"
  else
    echo "    note: process alive but no visible window yet (continuing)"
  fi
fi

echo "    OK: AppImage stayed up without Python traceback"
kill "$APID" 2>/dev/null || true
wait "$APID" 2>/dev/null || true
APID=""
