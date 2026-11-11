#!/usr/bin/env bash
# Build a relocatable GNOME prefix (GLib + GTK 4 + libadwaita) on an old base
# system so AppImages keep a low glibc floor while still shipping modern Adw.
#
# Usage:
#   ./appimage/build-gnome-prefix.sh [/path/to/prefix]
#
# Intended for Ubuntu 22.04 (glibc 2.35). Skips work when the prefix already
# has a new enough libadwaita.
set -euo pipefail

PREFIX="${1:-${GNOME_PREFIX:-$HOME/.cache/ready2rip-gnome-prefix}}"
SRC="${GNOME_SRC:-${PREFIX}.src}"
JOBS="${JOBS:-$(nproc 2>/dev/null || echo 2)}"

# Pin releases that provide Adw.Dialog / ToolbarView / AlertDialog / AboutDialog
# while remaining reasonably buildable on jammy toolchains.
GLIB_VER="${GLIB_VER:-2.78.6}"
GTK_VER="${GTK_VER:-4.14.5}"
ADW_VER="${ADW_VER:-1.5.3}"

mkdir -p "$PREFIX" "$SRC"
export PATH="${PREFIX}/bin:${PATH}"
export PKG_CONFIG_PATH="${PREFIX}/lib/pkgconfig:${PREFIX}/lib/$(uname -m)-linux-gnu/pkgconfig:${PKG_CONFIG_PATH:-}"
export LD_LIBRARY_PATH="${PREFIX}/lib:${PREFIX}/lib/$(uname -m)-linux-gnu:${LD_LIBRARY_PATH:-}"
export GI_TYPELIB_PATH="${PREFIX}/lib/girepository-1.0:${PREFIX}/lib/$(uname -m)-linux-gnu/girepository-1.0:${GI_TYPELIB_PATH:-}"
export XDG_DATA_DIRS="${PREFIX}/share:${XDG_DATA_DIRS:-/usr/share}"

have_adw() {
  pkg-config --exists "libadwaita-1 >= ${ADW_VER}" 2>/dev/null
}

if have_adw; then
  echo "==> GNOME prefix already OK at ${PREFIX} ($(pkg-config --modversion libadwaita-1))"
  exit 0
fi

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "error: missing build tool: $1" >&2
    exit 1
  }
}
need_cmd ninja
need_cmd curl
need_cmd tar
need_cmd pkg-config

# Ubuntu 22.04 ships Meson 0.61; GTK 4.14 needs >= 0.63.
if ! command -v meson >/dev/null 2>&1 || ! meson --version | awk -F. '{exit !($1>0 || $2>=63)}'; then
  echo "==> installing Meson >= 0.63 via pip"
  python3 -m pip install --user 'meson>=1.3,<2'
  export PATH="${HOME}/.local/bin:${PATH}"
fi
need_cmd meson
echo "    meson $(meson --version)"

fetch_tar() {
  local url="$1" dest="$2"
  if [ -d "$dest" ]; then
    return 0
  fi
  local tmp="${dest}.tar.partial"
  mkdir -p "$(dirname "$dest")"
  curl -fL --retry 3 -o "$tmp" "$url"
  rm -rf "$dest"
  mkdir -p "$dest"
  tar -xJf "$tmp" -C "$dest" --strip-components=1
  rm -f "$tmp"
}

meson_install() {
  local name="$1" srcdir="$2"
  shift 2
  local builddir="${srcdir}/_build"
  echo "==> building ${name}"
  rm -rf "$builddir"
  meson setup "$builddir" "$srcdir" \
    --prefix="$PREFIX" \
    --libdir=lib \
    --buildtype=release \
    "$@"
  meson compile -C "$builddir" -j "$JOBS"
  meson install -C "$builddir"
}

# --- GLib ---
if ! pkg-config --exists "glib-2.0 >= 2.76"; then
  fetch_tar \
    "https://download.gnome.org/sources/glib/${GLIB_VER%.*}/glib-${GLIB_VER}.tar.xz" \
    "${SRC}/glib-${GLIB_VER}"
  meson_install "glib-${GLIB_VER}" "${SRC}/glib-${GLIB_VER}" \
    -Dtests=false \
    -Dsysprof=disabled \
    -Dlibmount=enabled
fi

# --- GTK 4 ---
if ! pkg-config --exists "gtk4 >= 4.13.2"; then
  fetch_tar \
    "https://download.gnome.org/sources/gtk/${GTK_VER%.*}/gtk-${GTK_VER}.tar.xz" \
    "${SRC}/gtk-${GTK_VER}"
  meson_install "gtk-${GTK_VER}" "${SRC}/gtk-${GTK_VER}" \
    -Dintrospection=enabled \
    -Dbuild-tests=false \
    -Dbuild-testsuite=false \
    -Dbuild-examples=false \
    -Dbuild-demos=false \
    -Dmedia-gstreamer=disabled \
    -Dprint-cups=disabled \
    -Ddocumentation=false \
    -Dvulkan=disabled
fi

# --- libadwaita ---
if ! pkg-config --exists "libadwaita-1 >= ${ADW_VER}"; then
  fetch_tar \
    "https://download.gnome.org/sources/libadwaita/${ADW_VER%.*}/libadwaita-${ADW_VER}.tar.xz" \
    "${SRC}/libadwaita-${ADW_VER}"
  meson_install "libadwaita-${ADW_VER}" "${SRC}/libadwaita-${ADW_VER}" \
    -Dintrospection=enabled \
    -Dvapi=false \
    -Dtests=false \
    -Dexamples=false \
    -Dgtk_doc=false
fi

echo "==> GNOME prefix ready: ${PREFIX}"
pkg-config --modversion glib-2.0
pkg-config --modversion gtk4
pkg-config --modversion libadwaita-1
