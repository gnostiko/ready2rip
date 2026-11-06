# ready2rip

**ready2rip is a secure CD ripper for Linux.**

Careful archival extraction with full cdparanoia, AccurateRip and CTDB
verification, MusicBrainz / gnudb metadata, disc-native CD-TEXT / MCN / ISRC,
and modern tagging.

<img width="1050" height="750" alt="ready2rip" src="https://github.com/user-attachments/assets/fd305067-6bb0-426e-b712-7b2b62b59e5d" />


## What it does

| Feature | Description |
|---------|-------------|
| Secure rip | Full cdparanoia, retries, test & copy (CRC match required) |
| AccurateRip | Online CRC check after extraction (per-track and Copy image) |
| CTDB | CUETools Database verify; optional confirm / new-parity submit |
| Metadata | MusicBrainz + FreeDB-compatible lookup (e.g. gnudb); editable album and tracks |
| Disc extras | Live CD-TEXT, MCN (barcode), and per-track ISRC via MMC when the drive supports it |
| Tags | mutagen — FLAC / MP3 / AAC (M4A) / Opus / WAV (date, barcode, and ISRC when known) |
| ReplayGain | Track + album loudness tags |
| Artwork | Cover Art Archive and iTunes, with optional embed sizes |
| Logs & CUE | Detailed rip log; multi-file or image CUE (INDEX 00 pregaps, CATALOG / ISRC, mixed-mode data) |
| Drive setup | Sample offset calibration (required when AccurateRip or CTDB is on) |

## Typical workflow

1. **Install / run** — AppImage is the recommended path (see [Install](#install)). [Gear Lever](https://flathub.org/apps/it.mijorus.gearlever) adds menu integration and update checks.
2. **Insert an audio CD.** ready2rip detects the disc, reads the TOC, and shows tracks.
3. **Drive setup** (first run, or when you change drives): calibrate **sample offset**. If AccurateRip or CTDB is enabled, ripping is blocked until the drive is calibrated.
4. **Metadata** (optional): auto-lookup when a disc is detected, or open **Lookup → By disc ID** and pick a release. If the wrong album is matched, use **Lookup → MusicBrainz link…** and paste a `musicbrainz.org/release/…` URL or release UUID. Edit album/track fields if needed. Cover art can be fetched, chosen from disk, or cleared.
5. **Settings**: encoder, test & copy, CUE/log, Copy image, HTOA, and related options. Defaults already match a secure archival setup.
6. Press **Rip CD**. Progress appears in the bottom banner (status line shows track detail). After Detect from the offset gate, a successful calibration resumes the rip automatically.
7. Files land under your **output folder** using the album/track templates — audio, optional `cover`, `.log`, and `.cue`.

### What a secure rip does (under the hood)

ready2rip aims for secure archival ripping on Linux via cdparanoia / libcdio-paranoia:

| Step | Behaviour |
|------|-----------|
| Full paranoia | Overlap / jitter correction and multi-read repair (secure mode; not burst by default) |
| Never-skip + abort-on-skip | `--never-skip=500`, `-X`, and `-o 8` — keep re-reading imperfect data; force overlap search; don’t silently leave holes |
| Sample offset | Applied at extract time (`-O`) when calibrated (read offset correction) |
| Test and copy | Extract twice, compare CRC32; up to 3 secure retries on mismatch; defeat drive audio cache between passes; unmatched tracks fail the rip |
| AccurateRip | Verifies the offset-corrected audio against the public AR database |
| CTDB | Verifies track CRCs against the CUETools Database (complements AccurateRip) |
| Error logging | Parses cdparanoia progress into quality / fixups / skips / suspicious positions |
| Burst fallback | Off by default. Optional insecure `-Z` recovery after secure retries fail — noted in the log |

**Copy image** mode rips one continuous disc image (FLAC/WAV) instead of per-track files. AccurateRip and CTDB still run: the image is TOC-split into per-track PCM for verification (and CTDB submit when enabled). Enable **Write CUE sheet** for a matching image CUE — album tags on the image stay album-level; per-track ISRC lives in the CUE.

Per-track rips use a multi-file CUE with INDEX 00 pregaps appended to the previous track. Mixed-mode and CD-Extra data tracks are saved as raw `.bin` files (MODE1/2352 or MODE2/2352).

### Output layout

Defaults:

| Piece | Template / example |
|-------|--------------------|
| Album folder | `{album_artist}/{dated_album}/{disc_folder}` |
| Track file | `{track:02d} - {title}` → `01 - Title.flac` |
| Multi-disc | `Artist/(1999) - Album/CD1/…` and `…/CD2/…` with `CD1.cue` / `CD2.cue` beside the audio |

`{disc_folder}` is empty on single-disc releases. Filenames always use per-disc track numbers (`01`, `02`, …).

## Options

### Paths and naming

| Option | Default / notes |
|--------|-----------------|
| **Optical device** | Usually `/dev/sr0` |
| **Output folder** | `~/Music` |
| **Album folder scheme** | Presets (default `Artist / (Date) - Album` with `{disc_folder}`) or Custom — hover a preset for the template; tokens include `{album_artist}`, `{dated_album}`, `{disc_folder}`, `{disc}`, `{totaldiscs}`, … |
| **Track filename scheme** | Presets (default `01 - Title`) or Custom — VA preset adds `{artist}` |

### Encoder

| Option | Notes |
|--------|-------|
| **Encoder** | WAV, FLAC, Opus, M4A, or MP3 |
| **FLAC compression** | 0 (fast) … 8 (smallest); default **5** with `--verify` |
| **MP3 quality** | LAME V0–V5 (VBR) or 128–320 kbps CBR |
| **AAC quality** | FDK VBR 1–5 or 128–320 kbps CBR (`libfdk_aac` when available) |
| **Opus bitrate** | VBR kbps (typical 96–256) |

Copy image forces a lossless container (FLAC or WAV; other formats fall back to FLAC).

### Extraction

| Option | Default | Notes |
|--------|---------|-------|
| **Test and copy** | On | Two secure passes; matching CRCs required (retries on mismatch) |
| **Copy image** | Off | One continuous image instead of separate tracks; AR + CTDB still verify per track |
| **Pregap / HTOA** | On | Ignore ≤2s track-1 pause; longer non-silent pregap → track `00`; track 1 starts at index 01. Inter-track INDEX 00 is detected at rip start (cdrdao, else Q-subchannel) and written into the CUE |
| **Data tracks** | On | Mixed-mode / CD-Extra data as raw 2352-byte `.bin` with test & copy when that option is on; listed in the CUE. Failed data extract fails the rip |
| **AccurateRip** | On | Online CRC verification (hard-requires calibrated sample offset) |
| **CTDB** | On | CUETools Database track CRC verification (same offset requirement) |
| **Submit to CTDB** | Off | Confirm a full-disc match, or submit new parity when unmatched and quality is 100 |
| **Burst fallback** | Off | Optional insecure `-Z` recovery after secure retries fail |
| **Write rip log** | On | Detailed status log in the album folder |
| **Write CUE sheet** | On | Multi-file CUE with INDEX 00 in the previous file, or image CUE when Copy image is on |
| **Auto-rip** | Off | Start rip shortly after a new disc is detected |
| **Auto-eject** | Off | Open tray after a successful rip |

### Drive / calibration

Sample offset comes from **Drive setup** (offset scan) or a manual value from the public
[AccurateRip drive offset list](http://www.accuraterip.com/driveoffsets.htm).
When AccurateRip or CTDB is on, an uncalibrated drive cannot start a rip until Detect (or a manual offset save) succeeds.

### Metadata & art

- **Look up automatically** — query when a disc is detected
- **MusicBrainz / FreeDB** — sources for lookup (MusicBrainz ISRCs when present; FreeDB disc N/M inferred from title / EXTD markers such as `(disc 2)`)
- **Disc gap-fill** — CD-TEXT titles/artists, MCN barcode, and subchannel ISRC when online metadata is blank
- **Download artwork** — iTunes, Cover Art Archive
- **Embed artwork** — write cover into audio files; configurable max embed edge size
- **ReplayGain** — track + album tags after the rip set is complete

## Install

Release assets are published for **x86_64** and **aarch64** on
[GitHub Releases](https://github.com/gnostiko/ready2rip/releases):

| Format | Files | Best for |
|--------|-------|----------|
| **AppImage** | `ready2rip-*-<arch>.AppImage` (+ `.zsync`) | Portable installs; GPG-signed; recommended default |
| **Debian** | `ready2rip_*-1_amd64.deb` / `_arm64.deb` | Ubuntu / Debian using distro GTK and encoders |
| **RPM** | `ready2rip-*-1.x86_64.rpm` / `.aarch64.rpm` | Fedora and related distributions |
| **Flatpak** | `ready2rip-*-<arch>.flatpak` | Sandboxed install with bundled runtime and tools |

### AppImage (recommended)

```bash
chmod +x ready2rip-0.4.29-x86_64.AppImage   # or …-aarch64.AppImage
./ready2rip-0.4.29-x86_64.AppImage
```

The AppImage needs a matching CPU architecture, a host **glibc** new enough for
the build (glibc is not bundled — see [Portability](#appimage-portability)),
**FUSE** (`libfuse2` / `fuse`), an optical drive with your user in the `cdrom`
group (or equivalent), and a normal GPU/display stack (Mesa / Wayland / X11).

For desktop icons, menus, and updates from GitHub, use
**[Gear Lever](https://flathub.org/apps/it.mijorus.gearlever)**:

```bash
flatpak install flathub it.mijorus.gearlever
```

Open the AppImage in Gear Lever. ready2rip embeds update information at build
time so Gear Lever can detect GitHub Releases when both the `.AppImage` and
`.AppImage.zsync` assets are published.

#### Verifying the AppImage signature

Release AppImages are GPG-signed. Import the project key from
[`packaging/ready2rip-signing-key.asc`](packaging/ready2rip-signing-key.asc)
(fingerprint `D0391EB1F5B61355C9532B26859C0EE899C00AE3`), then validate with the
`validate-*.AppImage` tools from the
[appimagetool continuous release](https://github.com/AppImage/appimagetool/releases).

### Debian / Ubuntu

```bash
sudo apt install ./ready2rip_0.4.29-1_amd64.deb   # or _arm64.deb
```

Depends on distro packages for GTK 4 / libadwaita, mutagen, Pillow, cdparanoia,
flac, lame, ffmpeg, and opus-tools.

### Fedora / RPM

```bash
sudo dnf install ./ready2rip-0.4.29-1.x86_64.rpm   # or .aarch64.rpm
```

Same runtime idea as the `.deb`: system UI stack and encoders, ready2rip itself
from the package.

### Flatpak

```bash
flatpak install --user ./ready2rip-0.4.29-x86_64.flatpak
flatpak run org.ready2rip.Ready2Rip
```

The bundle targets GNOME Platform 51, ships libcdio-paranoia / flac / lame, and
uses the runtime’s codecs-extra stack (includes ffmpeg) for AAC and Opus.

## Dependencies

### End users (AppImage)

Always required on the **host** (not shipped in the image):

| Requirement | Why |
|-------------|-----|
| Matching arch (`x86_64` or `aarch64`) | Native AppImage |
| **glibc** new enough for the build | See [Portability](#appimage-portability); `GLIBC_2.xx not found` means the image is newer than your distro |
| Optical drive + `cdrom` (or equivalent) | Read / eject the disc |
| **FUSE** (`libfuse2` / `fuse`) | Mount the AppImage (preferred over `APPIMAGE_EXTRACT_AND_RUN=1`) |
| Graphics stack (Mesa / Vulkan / libGL, Wayland or X11) | Display |

What the AppImage **tries to ship** (so you usually need zero encode packages):

| Inside the image | Guaranteed? |
|------------------|-------------|
| Python, GTK 4, libadwaita, mutagen, Pillow | Yes (required at build) |
| **cdparanoia** | Yes (build fails without it) |
| **flac**, **lame**, **ffmpeg**, **opusenc** | Only if present on the build host — otherwise omitted |

### Develop / run from source

| Dependency | Role |
|------------|------|
| Python 3 + **PyGObject** (Gtk 4, Adw 1) | UI |
| **cdparanoia** or **cd-paranoia** | TOC + secure extract |
| **mutagen** | Tags + ReplayGain write |
| **Pillow** | Cover-art JPEG conversion |
| **flac** | FLAC encode |
| **lame** | MP3 encode |
| **ffmpeg** | AAC (M4A), ReplayGain analysis (`ebur128`), Opus fallback |
| **opusenc** | Opus encode (preferred over ffmpeg for Opus) |
| Meson, Ninja, gcc | Install / packaging |

Optional: **libfdk_aac** (in ffmpeg) for higher-quality AAC with VBR + afterburner;
**libdiscid** for DiscID helpers via ctypes (pure-Python TOC IDs are used if missing).

```bash
# Fedora
sudo dnf install python3-gobject gtk4 libadwaita python3-mutagen python3-pillow \
  cdparanoia flac lame ffmpeg opus-tools

# Debian / Ubuntu
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-girepository-3.0 \
  python3-mutagen python3-pil cdparanoia flac lame ffmpeg opus-tools
```

### Build host (AppImage packaging)

| Required on build host | Why |
|------------------------|-----|
| `python3`, PyGObject, GTK 4, libadwaita | Bundled into the image |
| `cdparanoia` / `cd-paranoia` | Bundled — build fails if missing |
| `meson`, `ninja`, `gcc`, `curl`, `pip` | Packaging |
| `flac`, `lame`, `ffmpeg`, `opusenc` | Install before building so they are copied into the image |
| `mutagen`, `Pillow` | Bundled |
| `zsync` / **zsyncmake** | Recommended — produces `.zsync` for Gear Lever delta updates |
| `gpg` | Required when signing (`SIGN=1`) |

```bash
./appimage/build-appimage.sh
# → dist/ready2rip-VERSION-<arch>.AppImage
```

The script **warns** (does not fail) if `flac` / `lame` / `ffmpeg` / `opusenc`
are missing. Check the build log for `warning: flac not on host` before publishing.

Native packages and Flatpak:

```bash
./packaging/build-deb-rpm.sh     # → dist/*.deb and dist/*.rpm
./packaging/build-flatpak.sh     # → dist/ready2rip-VERSION-<arch>.flatpak
```

### AppImage portability

Bundled and largely independent of the host: Python + stdlib + PyGObject, the
ready2rip package, mutagen, Pillow, GTK 4 / Adwaita (best-effort), and
cdparanoia. Encoders are included only when the **build host** had them.

What is **not** fully portable:

| Constraint | Why |
|------------|-----|
| **glibc version** | Linux cannot safely ship its own glibc in an AppImage. The image needs a host glibc ≥ the newest symbol used by bundled libs. Prefer building releases on the oldest supported target (or a container of that vintage) for wider reach. |
| **GPU / display stack** | Mesa, libGL, Vulkan, Wayland/X11 client libs come from the host. |
| **Architecture** | Separate AppImages for `x86_64` and `aarch64`. |

Check your system glibc with `ldd --version`. If the AppImage fails with
`GLIBC_2.xx not found`, rebuild on an older base or run on a newer distro.

## Develop from source

GSettings schemas compile into the user cache on first launch. From the repo:

```bash
./run
# equivalent: PYTHONPATH=src python3 -m ready2rip.main
```

Meson install / packaging:

```bash
meson setup build
meson compile -C build
meson compile -C build run          # optional: run via the build env
meson setup build --prefix="$PWD/install" --reconfigure
meson install -C build
./install/bin/ready2rip
```

```text
ready2rip/
  run                   # source-tree launcher
  appimage/             # AppRun + build-appimage.sh
  packaging/            # deb/rpm (nfpm) + Flatpak helpers
  flatpak/              # Flatpak manifest
  data/                 # desktop, icons, AppStream, GSettings
  src/ready2rip/
  meson.build
```

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).
