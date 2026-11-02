#!/usr/bin/env bash
# Build .deb and .rpm from a Meson DESTDIR install via nfpm.
#
# Usage (from repo root):
#   ./packaging/build-deb-rpm.sh
#
# Output:
#   dist/ready2rip_VERSION_ARCH.deb
#   dist/ready2rip-VERSION-1.ARCH.rpm
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

VERSION="$(grep -E "version: '" meson.build | head -1 | sed -E "s/.*version: '([^']+)'.*/\1/")"
HOST_ARCH="$(uname -m)"
case "$HOST_ARCH" in
  x86_64)
    NFPM_ARCH=amd64
    RPM_ARCH=x86_64
    ;;
  aarch64)
    NFPM_ARCH=arm64
    RPM_ARCH=aarch64
    ;;
  *)
    echo "error: unsupported architecture ${HOST_ARCH}" >&2
    exit 1
    ;;
esac

DIST="${ROOT}/dist"
STAGE="${ROOT}/build-packages/stage"
BUILD_DIR="${ROOT}/build-packages/meson"
TOOLS="${ROOT}/.tools"
mkdir -p "$DIST" "$TOOLS"

need() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "error: missing required tool: $1" >&2
    exit 1
  fi
}
need python3
need meson
need ninja
need curl
need envsubst

NFPM_BIN="${TOOLS}/nfpm"
if [ ! -x "$NFPM_BIN" ]; then
  case "$HOST_ARCH" in
    x86_64) NFPM_ASSET="nfpm_2.48.0_Linux_x86_64.tar.gz" ;;
    aarch64) NFPM_ASSET="nfpm_2.48.0_Linux_arm64.tar.gz" ;;
  esac
  echo "==> downloading nfpm (${NFPM_ASSET})"
  curl -fL --retry 3 -o "${TOOLS}/${NFPM_ASSET}" \
    "https://github.com/goreleaser/nfpm/releases/download/v2.48.0/${NFPM_ASSET}"
  tar -xzf "${TOOLS}/${NFPM_ASSET}" -C "$TOOLS" nfpm
  chmod +x "$NFPM_BIN"
fi

echo "==> meson install → ${STAGE}"
rm -rf "$STAGE" "$BUILD_DIR"
meson setup "$BUILD_DIR" --prefix=/usr
DESTDIR="$STAGE" meson install -C "$BUILD_DIR"
# Drop any bytecode Meson copied from the source tree.
find "$STAGE" -type d -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true
find "$STAGE" -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete 2>/dev/null || true

export VERSION NFPM_ARCH STAGE ROOT
envsubst '${VERSION} ${NFPM_ARCH} ${STAGE} ${ROOT}' \
  < "${ROOT}/packaging/nfpm.yaml.in" > "${ROOT}/build-packages/nfpm.yaml"

echo "==> nfpm package deb (${NFPM_ARCH})"
"$NFPM_BIN" package \
  --config "${ROOT}/build-packages/nfpm.yaml" \
  --packager deb \
  --target "$DIST"

# RPM arch field uses rpm names (x86_64 / aarch64), not deb (amd64 / arm64).
export NFPM_ARCH="$RPM_ARCH"
envsubst '${VERSION} ${NFPM_ARCH} ${STAGE} ${ROOT}' \
  < "${ROOT}/packaging/nfpm.yaml.in" > "${ROOT}/build-packages/nfpm-rpm.yaml"

echo "==> nfpm package rpm (${RPM_ARCH})"
"$NFPM_BIN" package \
  --config "${ROOT}/build-packages/nfpm-rpm.yaml" \
  --packager rpm \
  --target "$DIST"

echo "==> packages:"
ls -la "$DIST"/ready2rip*.deb "$DIST"/ready2rip*.rpm
