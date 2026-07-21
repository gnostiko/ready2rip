#!/usr/bin/env bash
# Build a portable ready2rip AppImage (x86_64).
#
# Usage (from repo root):
#   ./appimage/build-appimage.sh
#
# Output:
#   dist/ready2rip-VERSION-x86_64.AppImage
#
# Build-host requirements:
#   python3, pip, meson, ninja, gcc, curl
#   PyGObject + GTK 4 + libadwaita (bundled into the image)
#   cdparanoia or cd-paranoia, flac, lame, ffmpeg (recommended; bundled)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

APP_ID="org.ready2rip.Ready2Rip"
APP_NAME="ready2rip"
VERSION="$(grep -E "version: '" meson.build | head -1 | sed -E "s/.*version: '([^']+)'.*/\1/")"
ARCH="$(uname -m)"
APPDIR="${ROOT}/build-appimage/AppDir"
DIST="${ROOT}/dist"
TOOLS="${ROOT}/.tools"
OUT_NAME="${APP_NAME}-${VERSION}-${ARCH}.AppImage"

# GitHub Releases update info (AppImageUpdate / Gear Lever / appimageupdatetool).
# Format: gh-releases-zsync|<owner>|<repo>|<tag>|<zsync asset name>
# Override with GITHUB_REPOSITORY=owner/repo when building from CI.
GH_REPO="${GITHUB_REPOSITORY:-gnostiko/ready2rip}"
GH_OWNER="${GH_REPO%%/*}"
GH_NAME="${GH_REPO#*/}"
# Wildcard matches versioned assets on the release (Gear Lever uses fnmatch).
ZSYNC_ASSET="${APP_NAME}-*-${ARCH}.AppImage.zsync"
UPDATE_INFORMATION="gh-releases-zsync|${GH_OWNER}|${GH_NAME}|latest|${ZSYNC_ASSET}"
export UPDATE_INFORMATION
export UPD_INFO="${UPDATE_INFORMATION}"

mkdir -p "$TOOLS" "$DIST"

echo "==> ready2rip AppImage ${VERSION} (${ARCH})"
echo "    AppDir: ${APPDIR}"
echo "    Update: ${UPDATE_INFORMATION}"

need() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "error: missing required tool: $1" >&2
    exit 1
  fi
}
need python3
need meson
need ninja
need gcc
need curl
if ! command -v pip3 >/dev/null 2>&1 && ! command -v pip >/dev/null 2>&1; then
  echo "error: missing pip" >&2
  exit 1
fi
if ! command -v zsyncmake >/dev/null 2>&1; then
  echo "warning: zsyncmake not found — install package 'zsync' for Gear Lever delta updates" >&2
  echo "         (update info is still embedded; manual Gear Lever GitHub URL works either way)" >&2
fi

# ---------------------------------------------------------------------------
# Packaging tools
# ---------------------------------------------------------------------------
download() {
  local url="$1" dest="$2"
  if [ -x "$dest" ]; then
    return 0
  fi
  echo "==> downloading $(basename "$dest")"
  curl -fL --retry 3 -o "${dest}.partial" "$url"
  mv "${dest}.partial" "$dest"
  chmod +x "$dest"
}

if [ "$ARCH" != "x86_64" ]; then
  echo "error: x86_64 AppImages only (got ${ARCH})" >&2
  exit 1
fi

download \
  "https://github.com/linuxdeploy/linuxdeploy/releases/download/continuous/linuxdeploy-x86_64.AppImage" \
  "${TOOLS}/linuxdeploy"
download \
  "https://github.com/linuxdeploy/linuxdeploy-plugin-gtk/raw/master/linuxdeploy-plugin-gtk.sh" \
  "${TOOLS}/linuxdeploy-plugin-gtk.sh"
download \
  "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage" \
  "${TOOLS}/appimagetool"

# Build-tool only (do not leak into the final AppImage runtime).
export APPIMAGE_EXTRACT_AND_RUN=1

# ---------------------------------------------------------------------------
# Meson install into AppDir
# ---------------------------------------------------------------------------
echo "==> meson install → AppDir"
rm -rf "$APPDIR"
mkdir -p "$APPDIR"
BUILD_DIR="${ROOT}/build-appimage/meson"
rm -rf "$BUILD_DIR"
meson setup "$BUILD_DIR" --prefix=/usr
DESTDIR="$APPDIR" meson install -C "$BUILD_DIR"

if [ ! -d "${APPDIR}/usr" ]; then
  echo "error: expected ${APPDIR}/usr after meson install" >&2
  exit 1
fi

# Drop bytecode trees (Meson may still copy nested __pycache__)
find "$APPDIR" -type d -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true

mkdir -p "${APPDIR}/usr/bin" \
  "${APPDIR}/usr/lib" \
  "${APPDIR}/usr/lib/girepository-1.0" \
  "${APPDIR}/usr/lib/python3/site-packages" \
  "${APPDIR}/usr/share/applications" \
  "${APPDIR}/usr/share/glib-2.0/schemas"

# ---------------------------------------------------------------------------
# Python deps
# ---------------------------------------------------------------------------
echo "==> pip install mutagen"
python3 -m pip install --upgrade --no-compile \
  --target="${APPDIR}/usr/lib/python3/site-packages" \
  "mutagen>=1.47"

# ---------------------------------------------------------------------------
# Bundle PyGObject (gi) from the build host
# ---------------------------------------------------------------------------
echo "==> bundling PyGObject (gi)"
python3 - "$APPDIR" <<'PY'
import shutil
import sys
from pathlib import Path

appdir = Path(sys.argv[1])
try:
    import gi
except ImportError as exc:
    print(f"error: PyGObject (gi) not installed on build host: {exc}", file=sys.stderr)
    sys.exit(1)

src_root = Path(gi.__file__).resolve().parent  # .../site-packages/gi
site = src_root.parent
dest_site = appdir / "usr/lib/python3/site-packages"
dest_site.mkdir(parents=True, exist_ok=True)

dst_gi = dest_site / "gi"
if dst_gi.exists():
    shutil.rmtree(dst_gi)
shutil.copytree(src_root, dst_gi, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
print(f"    gi → {dst_gi}")

for extra in ("pygtkcompat",):
    src = site / extra
    if src.is_dir():
        dst = dest_site / extra
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        print(f"    {extra} → {dst}")
PY

# ---------------------------------------------------------------------------
# Bundle GI typelibs required at runtime
# ---------------------------------------------------------------------------
echo "==> bundling GObject Introspection typelibs"
TYPELIB_DIRS=()
for d in \
  /usr/lib/girepository-1.0 \
  /usr/lib64/girepository-1.0 \
  /usr/lib/x86_64-linux-gnu/girepository-1.0
do
  [ -d "$d" ] && TYPELIB_DIRS+=("$d")
done
if [ "${#TYPELIB_DIRS[@]}" -eq 0 ]; then
  echo "error: no girepository-1.0 directory on build host" >&2
  exit 1
fi

# Names are case-sensitive (cairo-1.0 is lowercase on most distros).
REQUIRED_TYPELIBS=(
  Gtk-4.0 Adw-1 Gdk-4.0 Gsk-4.0
  GLib-2.0 GObject-2.0 Gio-2.0 GModule-2.0
  GdkPixbuf-2.0 Graphene-1.0 HarfBuzz-0.0
  Pango-1.0 PangoCairo-1.0
  cairo-1.0 freetype2-2.0 fontconfig-2.0
  xlib-2.0
  GIRepository-2.0 GIRepository-3.0
)
# Optional but useful on Wayland/X11 hosts
OPTIONAL_TYPELIBS=(
  GdkWayland-4.0 GdkX11-4.0
  Soup-3.0 Soup-2.4 Json-1.0
)

copy_typelib() {
  local name="$1" required="$2"
  local found=""
  for dir in "${TYPELIB_DIRS[@]}"; do
    if [ -f "${dir}/${name}.typelib" ]; then
      found="${dir}/${name}.typelib"
      break
    fi
  done
  if [ -n "$found" ]; then
    install -D -m644 "$found" \
      "${APPDIR}/usr/lib/girepository-1.0/${name}.typelib"
    return 0
  fi
  if [ "$required" = "1" ]; then
    echo "error: missing required typelib ${name}.typelib" >&2
    return 1
  fi
  echo "    optional typelib missing: ${name}"
  return 0
}

for t in "${REQUIRED_TYPELIBS[@]}"; do
  copy_typelib "$t" 1
done
for t in "${OPTIONAL_TYPELIBS[@]}"; do
  copy_typelib "$t" 0 || true
done

# ---------------------------------------------------------------------------
# Helpers: copy shared libraries and binaries (via ldd)
# ---------------------------------------------------------------------------
copy_lib() {
  local src="$1"
  [ -f "$src" ] || return 1
  local base
  base="$(basename "$src")"
  if [ ! -e "${APPDIR}/usr/lib/${base}" ]; then
    install -D -m755 "$src" "${APPDIR}/usr/lib/${base}" 2>/dev/null || return 1
  fi
  return 0
}

bundle_ldd_deps() {
  local real
  real="$(readlink -f "$1")"
  ldd "$real" 2>/dev/null | awk '
    /=> \// { print $3 }
    /^\t\// { print $1 }
  ' | while read -r lib; do
    [ -n "$lib" ] || continue
    [ -f "$lib" ] || continue
    case "$lib" in
      *ld-linux*) continue ;;
      *linux-vdso*) continue ;;
    esac
    copy_lib "$lib" || true
  done
}

copy_bin_with_libs() {
  local src="$1"
  local dest_name="${2:-$(basename "$src")}"
  if [ ! -x "$src" ]; then
    return 1
  fi
  echo "    bundling $src → usr/bin/${dest_name}"
  install -D -m755 "$src" "${APPDIR}/usr/bin/${dest_name}"
  bundle_ldd_deps "$src"
  return 0
}

echo "==> bundling helper binaries + libraries"
# Shared libs used by gi._gi*.so (must run after bundle_ldd_deps is defined)
find "${APPDIR}/usr/lib/python3/site-packages/gi" -name '*.so' 2>/dev/null | while read -r so; do
  bundle_ldd_deps "$so" || true
done

HOST_PYTHON="$(readlink -f "$(command -v python3)")"
copy_bin_with_libs "$HOST_PYTHON" "python3" || true
# Also keep versioned name if different
if [ -x /usr/bin/python3.14 ]; then
  copy_bin_with_libs /usr/bin/python3.14 python3.14 || true
fi
# Symlink python3 → versioned if needed
if [ ! -e "${APPDIR}/usr/bin/python3" ] && [ -x "${APPDIR}/usr/bin/python3.14" ]; then
  ln -sfn python3.14 "${APPDIR}/usr/bin/python3"
fi

# libpython is critical
for lib in /usr/lib/libpython3*.so* /usr/lib64/libpython3*.so*; do
  [ -f "$lib" ] || continue
  install -D -m755 "$lib" "${APPDIR}/usr/lib/$(basename "$lib")"
done

# Optical / encode tools
if command -v cdparanoia >/dev/null 2>&1; then
  copy_bin_with_libs "$(command -v cdparanoia)" cdparanoia
elif command -v cd-paranoia >/dev/null 2>&1; then
  copy_bin_with_libs "$(command -v cd-paranoia)" cd-paranoia
  ln -sfn cd-paranoia "${APPDIR}/usr/bin/cdparanoia"
else
  echo "error: cdparanoia/cd-paranoia required on build host for a working AppImage" >&2
  exit 1
fi

for tool in flac lame ffmpeg eject opusenc; do
  if command -v "$tool" >/dev/null 2>&1; then
    copy_bin_with_libs "$(command -v "$tool")" "$tool" || true
  else
    echo "    warning: $tool not on host (optional)"
  fi
done

# libdiscid optional (MusicBrainz discid put path)
for lib in /usr/lib/libdiscid.so* /usr/lib64/libdiscid.so* /usr/lib/x86_64-linux-gnu/libdiscid.so*; do
  [ -f "$lib" ] || continue
  install -D -m755 "$lib" "${APPDIR}/usr/lib/$(basename "$lib")" 2>/dev/null || true
done

# GTK / Adwaita / related shared objects
echo "==> bundling GTK 4 / libadwaita libraries"
for soname in libgtk-4.so.1 libadwaita-1.so.0 libgdk_pixbuf-2.0.so.0 \
  libpango-1.0.so.0 libpangocairo-1.0.so.0 libgobject-2.0.so.0 \
  libglib-2.0.so.0 libgio-2.0.so.0 libgmodule-2.0.so.0 \
  libgraphene-1.0.so.1 libharfbuzz.so.0 libepoxy.so.0 \
  libcairo.so.2 libfribidi.so.0 libcloudproviders.so.0; do
  path="$(ldconfig -p 2>/dev/null | awk -v n="$soname" '$1==n {print $NF; exit}')"
  if [ -n "$path" ] && [ -f "$path" ]; then
    echo "    lib $path"
    copy_lib "$path"
    bundle_ldd_deps "$path"
  else
    echo "    warning: $soname not found on host"
  fi
done
# Never leave shared objects in usr/bin
find "${APPDIR}/usr/bin" -name 'lib*.so*' -delete 2>/dev/null || true

# AccurateRip offset helper
AR_SRC="${ROOT}/src/ready2rip/native/ar_offset_scan.c"
if [ -f "$AR_SRC" ]; then
  echo "==> building ar_offset_scan"
  gcc -O3 -o "${APPDIR}/usr/bin/ar_offset_scan" "$AR_SRC"
fi

# ---------------------------------------------------------------------------
# Desktop entry + icon
# ---------------------------------------------------------------------------
echo "==> desktop entry and icon"
install -D -m644 "${ROOT}/appimage/org.ready2rip.Ready2Rip.desktop" \
  "${APPDIR}/${APP_ID}.desktop"
sed -i 's/^Exec=.*/Exec=ready2rip/' "${APPDIR}/${APP_ID}.desktop"
install -D -m644 "${ROOT}/appimage/org.ready2rip.Ready2Rip.desktop" \
  "${APPDIR}/usr/share/applications/${APP_ID}.desktop"
sed -i 's/^Exec=.*/Exec=ready2rip/' \
  "${APPDIR}/usr/share/applications/${APP_ID}.desktop"

install -D -m644 \
  "${ROOT}/data/icons/hicolor/scalable/apps/${APP_ID}.svg" \
  "${APPDIR}/usr/share/icons/hicolor/scalable/apps/${APP_ID}.svg"
cp -f "${ROOT}/data/icons/hicolor/scalable/apps/${APP_ID}.svg" \
  "${APPDIR}/${APP_ID}.svg"

if command -v glib-compile-schemas >/dev/null 2>&1; then
  echo "==> glib-compile-schemas"
  glib-compile-schemas "${APPDIR}/usr/share/glib-2.0/schemas"
else
  echo "error: glib-compile-schemas required" >&2
  exit 1
fi

install -D -m755 "${ROOT}/appimage/AppRun.in" "${APPDIR}/AppRun"

# Ensure ready2rip launcher exists (Meson should install it)
if [ ! -x "${APPDIR}/usr/bin/ready2rip" ]; then
  cat > "${APPDIR}/usr/bin/ready2rip" <<'EOF'
#!/bin/sh
DIR="$(cd "$(dirname "$(readlink -f "$0")")/../.." && pwd)"
exec "${DIR}/AppRun" "$@"
EOF
  chmod +x "${APPDIR}/usr/bin/ready2rip"
fi

# ---------------------------------------------------------------------------
# linuxdeploy for remaining deps + gtk plugin (best effort)
# ---------------------------------------------------------------------------
echo "==> linuxdeploy"
export LINUXDEPLOY_PLUGIN_GTK="${TOOLS}/linuxdeploy-plugin-gtk.sh"
# Newer glibc RELR sections make linuxdeploy's bundled strip fail noisily.
export NO_STRIP=1
set +e
"${TOOLS}/linuxdeploy" --appdir="$APPDIR" \
  --executable="${APPDIR}/usr/bin/python3" \
  --executable="${APPDIR}/usr/bin/cdparanoia" \
  --executable="${APPDIR}/usr/bin/flac" \
  --desktop-file="${APPDIR}/usr/share/applications/${APP_ID}.desktop" \
  --icon-file="${APPDIR}/usr/share/icons/hicolor/scalable/apps/${APP_ID}.svg" \
  --plugin gtk
LD_RC=$?
if [ "$LD_RC" -ne 0 ]; then
  echo "warning: linuxdeploy gtk plugin failed; retrying without plugin" >&2
  "${TOOLS}/linuxdeploy" --appdir="$APPDIR" \
    --executable="${APPDIR}/usr/bin/python3" \
    --executable="${APPDIR}/usr/bin/cdparanoia" \
    --desktop-file="${APPDIR}/usr/share/applications/${APP_ID}.desktop" \
    --icon-file="${APPDIR}/usr/share/icons/hicolor/scalable/apps/${APP_ID}.svg"
  LD_RC=$?
fi
set -e
if [ "$LD_RC" -ne 0 ]; then
  echo "warning: linuxdeploy failed; continuing with manual bundles" >&2
fi

# linuxdeploy may overwrite AppRun / desktop
install -D -m755 "${ROOT}/appimage/AppRun.in" "${APPDIR}/AppRun"
cp -f "${ROOT}/appimage/org.ready2rip.Ready2Rip.desktop" "${APPDIR}/${APP_ID}.desktop"
sed -i 's/^Exec=.*/Exec=ready2rip/' "${APPDIR}/${APP_ID}.desktop"
cp -f "${ROOT}/data/icons/hicolor/scalable/apps/${APP_ID}.svg" "${APPDIR}/${APP_ID}.svg"

# ---------------------------------------------------------------------------
# Smoke-test inside AppDir (no FUSE needed)
# ---------------------------------------------------------------------------
echo "==> smoke-test AppDir imports"
set +e
(
  export APPDIR="$APPDIR"
  export PATH="${APPDIR}/usr/bin:$PATH"
  export PYTHONPATH="${APPDIR}/usr/share/ready2rip:${APPDIR}/usr/lib/python3/site-packages"
  for d in "${APPDIR}/usr/lib"/python3.*/site-packages; do
    [ -d "$d" ] && PYTHONPATH="$d:$PYTHONPATH"
  done
  export LD_LIBRARY_PATH="${APPDIR}/usr/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
  export GI_TYPELIB_PATH="${APPDIR}/usr/lib/girepository-1.0${GI_TYPELIB_PATH:+:$GI_TYPELIB_PATH}"
  export GSETTINGS_SCHEMA_DIR="${APPDIR}/usr/share/glib-2.0/schemas"
  PY="${APPDIR}/usr/bin/python3"
  [ -x "$PY" ] || PY=python3
  "$PY" -B - <<'PY'
import sys
print("python", sys.version.split()[0], file=sys.stderr)
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib  # noqa: F401
import mutagen  # noqa: F401
import ready2rip.main  # noqa: F401
from ready2rip.util import find_cdparanoia
cp = find_cdparanoia()
assert cp, "cdparanoia not found on PATH inside AppDir"
print("ok: Gtk/Adw/mutagen/ready2rip/cdparanoia", cp, file=sys.stderr)
PY
)
SMOKE=$?
set -e
if [ "$SMOKE" -ne 0 ]; then
  echo "error: AppDir smoke-test failed — fix packaging before release" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Pack (embed GitHub update info for Gear Lever / AppImageUpdate)
# ---------------------------------------------------------------------------
echo "==> appimagetool (update info for Gear Lever)"
rm -f "${DIST}/${OUT_NAME}" "${DIST}/${OUT_NAME}.zsync"
install -D -m755 "${ROOT}/appimage/AppRun.in" "${APPDIR}/AppRun"
# Avoid shipping extract leftovers
rm -rf "${ROOT}/squashfs-root" 2>/dev/null || true

if ! command -v zsyncmake >/dev/null 2>&1; then
  echo "    note: zsyncmake not on PATH — embedding update info only;"
  echo "          install zsync (zsyncmake) to also emit .AppImage.zsync for delta updates"
fi

# -u embeds .upd_info (read by Gear Lever + AppImageUpdate) and, when
# zsyncmake is available, writes a sibling .zsync next to the AppImage.
set +e
ARCH="$ARCH" "${TOOLS}/appimagetool" \
  --no-appstream \
  -u "${UPDATE_INFORMATION}" \
  "$APPDIR" \
  "${DIST}/${OUT_NAME}"
AT_RC=$?
if [ "$AT_RC" -ne 0 ]; then
  echo "warning: appimagetool with -u failed; retrying without update info" >&2
  ARCH="$ARCH" "${TOOLS}/appimagetool" --no-appstream \
    "$APPDIR" "${DIST}/${OUT_NAME}"
  AT_RC=$?
fi
set -e
if [ "$AT_RC" -ne 0 ]; then
  echo "error: appimagetool failed" >&2
  exit 1
fi

chmod +x "${DIST}/${OUT_NAME}"

# appimagetool may write the .zsync next to the AppImage or in CWD
if [ -f "${DIST}/${OUT_NAME}.zsync" ]; then
  :
elif [ -f "${OUT_NAME}.zsync" ]; then
  mv -f "${OUT_NAME}.zsync" "${DIST}/${OUT_NAME}.zsync"
elif [ -f "${ROOT}/${OUT_NAME}.zsync" ]; then
  mv -f "${ROOT}/${OUT_NAME}.zsync" "${DIST}/${OUT_NAME}.zsync"
fi

# Verify embedded update information (what Gear Lever reads via readelf)
if command -v readelf >/dev/null 2>&1; then
  EMBEDDED="$(readelf --string-dump=.upd_info --wide "${DIST}/${OUT_NAME}" 2>/dev/null | tr '\n' ' ' || true)"
  if echo "$EMBEDDED" | grep -q 'gh-releases-zsync'; then
    echo "    embedded .upd_info OK (Gear Lever can auto-detect GitHub updates)"
  else
    echo "    warning: .upd_info not found — Gear Lever needs a manual update URL"
  fi
fi

echo
echo "Built: ${DIST}/${OUT_NAME}"
ls -lh "${DIST}/${OUT_NAME}"
if [ -f "${DIST}/${OUT_NAME}.zsync" ]; then
  ls -lh "${DIST}/${OUT_NAME}.zsync"
  echo
  echo "GitHub Release assets (upload BOTH):"
  echo "  ${DIST}/${OUT_NAME}"
  echo "  ${DIST}/${OUT_NAME}.zsync"
else
  echo
  echo "GitHub Release asset:"
  echo "  ${DIST}/${OUT_NAME}"
  echo "  (no .zsync — install zsyncmake and rebuild for delta updates)"
fi
echo
echo "Gear Lever:"
echo "  Auto: open the AppImage in Gear Lever — update URL is embedded."
echo "  Manual: Github → ${GH_OWNER}/${GH_NAME} → ${APP_NAME}-*-${ARCH}.AppImage"
echo
echo "Run:"
echo "  ${DIST}/${OUT_NAME}"
echo
echo "Users need: executable bit (chmod +x). Prefer FUSE/libfuse2 for fast launch."
echo "Do not set APPIMAGE_EXTRACT_AND_RUN=1 for normal use (slow full extract)."
