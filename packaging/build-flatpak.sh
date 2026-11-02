#!/usr/bin/env bash
# Build a single-file Flatpak bundle for the current architecture.
#
# Usage (from repo root):
#   ./packaging/build-flatpak.sh
#
# Output:
#   dist/ready2rip-VERSION-<arch>.flatpak
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

VERSION="$(grep -E "version: '" meson.build | head -1 | sed -E "s/.*version: '([^']+)'.*/\1/")"
ARCH="$(uname -m)"
case "$ARCH" in
  x86_64|aarch64) ;;
  *)
    echo "error: unsupported architecture ${ARCH}" >&2
    exit 1
    ;;
esac

DIST="${ROOT}/dist"
BUILD_DIR="${ROOT}/build-flatpak"
REPO_DIR="${ROOT}/flatpak-repo"
MANIFEST="${ROOT}/flatpak/org.ready2rip.Ready2Rip.yml"
OUT="${DIST}/ready2rip-${VERSION}-${ARCH}.flatpak"

need() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "error: missing required tool: $1" >&2
    exit 1
  fi
}
need flatpak
need flatpak-builder

mkdir -p "$DIST"

echo "==> ensure Flathub remote + GNOME 49 SDK"
flatpak remote-add --user --if-not-exists flathub \
  https://dl.flathub.org/repo/flathub.flatpakrepo
flatpak install -y --user flathub \
  org.gnome.Platform//49 \
  org.gnome.Sdk//49

echo "==> flatpak-builder (${ARCH})"
rm -rf "$BUILD_DIR" "$REPO_DIR"
flatpak-builder --user --force-clean --install-deps-from=flathub \
  --repo="$REPO_DIR" \
  "$BUILD_DIR" \
  "$MANIFEST"

echo "==> build-bundle → ${OUT}"
rm -f "$OUT"
flatpak build-bundle "$REPO_DIR" "$OUT" org.ready2rip.Ready2Rip
ls -lh "$OUT"
echo "Install with: flatpak install --user ${OUT}"
