# ready2rip

**ready2rip is a secure CD ripper for Linux.**

Careful archival extraction with full cdparanoia, AccurateRip and CTDB
verification, MusicBrainz / gnudb metadata, disc-native CD-TEXT / MCN / ISRC,
and modern tagging.

<img width="1122" height="822" alt="ready2rip" src="https://github.com/user-attachments/assets/90a3db12-8bda-43b6-a82b-7e587c704efa" />

## What it does

| Feature | Description |
|---------|-------------|
| Secure rip | Full cdparanoia, retries, test & copy (CRC match required) |
| AccurateRip | Online CRC check after extraction (per-track and Copy image) |
| CTDB | CUETools Database verify; optional confirm / new-parity submit |
| Metadata | MusicBrainz + FreeDB-compatible lookup (e.g. gnudb); editable album and tracks |
| Disc extras | Live CD-TEXT, MCN (barcode), and per-track ISRC via MMC when the drive supports it |
| Tags | mutagen — FLAC / MP3 / AAC (M4A) / Opus / WAV / AIFF (date, barcode, and ISRC when known) |
| ReplayGain | Track + album loudness tags |
| Sound Check | iTunNORM on MP3 / M4A / AIFF / WAV for iPod / iTunes |
| Artwork | Cover Art Archive and iTunes, with optional embed sizes |
| Lyrics | Optional LRCLIB fetch; embed and/or sidecar for lossless |
| Logs & CUE | Detailed rip log; multi-file or image CUE (INDEX 00 pregaps, CATALOG / ISRC, mixed-mode data) |
| Drive setup | Sample offset calibration (required when AccurateRip or CTDB is on) |

## Typical workflow

1. **Install / run** — AppImage is the recommended path (see [Install](#install)). [Gear Lever](https://github.com/mijorus/gearlever) adds menu integration and update checks.
2. **Insert an audio CD.** ready2rip detects the disc, reads the TOC, and shows tracks.
3. **Drive setup** (first run, or when you change drives): calibrate **sample offset**. If AccurateRip or CTDB is enabled, ripping is blocked until the drive is calibrated.
4. **Metadata** (optional): auto-lookup when a disc is detected, or open **Lookup → By disc ID** and pick a release. If the wrong album is matched, use **Lookup → MusicBrainz link…** and paste a `musicbrainz.org/release/…` URL or release UUID. Edit album/track fields if needed. Cover art can be fetched, chosen from disk, or cleared.
5. **Settings**: encoder, test & copy, CUE/log, Copy image, HTOA, and related options. Defaults already match a secure archival setup.
6. Press **Rip**. Progress appears in the bottom banner (status line shows track detail). After Detect from the offset gate, a successful calibration resumes the rip automatically.
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

**Copy image** mode rips one continuous disc image (FLAC / WAV / AIFF) instead of per-track files. AccurateRip and CTDB still run: the image is TOC-split into per-track PCM for verification (and CTDB submit when enabled). Enable **Write CUE sheet** for a matching image CUE — album tags on the image stay album-level; per-track ISRC lives in the CUE. Lossy primary formats are not used for the image (they fall back to FLAC).

Per-track rips use a multi-file CUE with INDEX 00 pregaps appended to the previous track. Mixed-mode and CD-Extra data tracks are saved as raw `.bin` files (MODE1/2352 or MODE2/2352) beside the lossless output (not into Also-encode lossy folders).

### Output layout

Defaults:

| Piece | Template / example |
|-------|--------------------|
| Album folder | `{album_artist}/{dated_album}/{disc_folder}` |
| Track file | `{track:02d} - {title}` → `01 - Title.flac` |
| Multi-disc | `Artist/(1999) - Album/CD1/…` and `…/CD2/…` with `CD1.cue` / `CD2.cue` beside the audio |

`{disc_folder}` is empty on single-disc releases. Filenames always use per-disc track numbers (`01`, `02`, …). `{dated_album}` is `(YYYY-MM-DD) - Album` when a date is known, otherwise the album title.

## Options

### Paths and naming

| Option | Default / notes |
|--------|-----------------|
| **Optical device** | Usually `/dev/sr0` |
| **Output folder** | XDG Music (typically `~/Music`) when unset |
| **Existing files** | **Overwrite** (default), **Ask** before replacing, **Skip existing**, or **Keep both** (`Title (2).ext`) |
| **Album folder scheme** | Presets (default `Artist / (Date) - Album` with `{disc_folder}`) or Custom — hover a preset for the template; tokens include `{album_artist}`, `{dated_album}`, `{disc_folder}`, `{disc}`, `{totaldiscs}`, `{label}`, … |
| **Track filename scheme** | Presets (default `01 - Title`) or Custom — VA preset adds `{artist}`; tokens include `{track:02d}`, `{disc_track}`, `{title}`, … |
| **Artist style** | How multiple artists appear in filenames (`semicolon`, `slash`, or `feat` in the title) |

### Encoding

| Option | Notes |
|--------|-------|
| **Primary format** | Always lossless / PCM: WAV, AIFF, or FLAC (default **FLAC**) |
| **Also encode** | Optional second pass: LAME MP3, FDK AAC, QAAC, or Opus into a format subfolder, the same album folder, or a custom root |
| **FLAC compression** | 0 (Fastest) … 8 (Smallest); default **5 (Default)** with `--verify` |
| **MP3 quality** | LAME V5–V0 (VBR, lowest→highest) or 128–320 kbps CBR (default **V0**) |
| **AAC quality** | FDK VBR 1–5 or 128–320 kbps CBR (`libfdk_aac` when available; default **256 CBR**); lowest→highest |
| **QAAC quality** | Opt-in Apple AAC via Wine: TVBR 82/91/100, CVBR 256 (default), CBR 256 — not bundled |
| **Opus bitrate** | VBR kbps (typical 96–256; default **160**); lowest→highest |

Copy image forces a lossless/PCM container (FLAC, WAV, or AIFF; other formats fall back to FLAC). Also-encode companions are not produced in Copy image mode.

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

- **Look up automatically** — query when a disc is detected (default on)
- **MusicBrainz / FreeDB** — sources for lookup (MusicBrainz ISRCs when present; FreeDB disc N/M inferred from title / EXTD markers such as `(disc 2)`)
- **Disc gap-fill** — CD-TEXT titles/artists, MCN barcode, and subchannel ISRC when online metadata is blank
- **Download artwork** — iTunes, Cover Art Archive (default on)
- **Embed artwork** — separate lossy / lossless options with their own sizes (300–800 px, default 600); lossy embeds only (no loose `cover.jpg`); lossless also writes full-res folder cover when embedding is on
- **ReplayGain** — track + album tags after the rip set is complete (default on)
- **iTunes Sound Check** — iTunNORM on MP3 / M4A / AIFF / WAV (from track ReplayGain; default on)
- **Lyrics** — optional LRCLIB fetch after metadata (default off); lossless save mode (embed tags, sidecar files, or both — default both)
- **Encoded by** — always written (codec tool + version: flac, LAME, ffmpeg/FDK, qaac, opusenc, …)
- **Ripper tag** — default on; writes `ready2rip <version>` into the comment field

## Install

Release assets are published for **x86_64** and **aarch64** on
[GitHub Releases](https://github.com/gnostiko/ready2rip/releases):

| Format | Files | Best for |
|--------|-------|----------|
| **AppImage** | `ready2rip-*-<arch>.AppImage` (+ `.zsync`) | Portable installs; GPG-signed; recommended default |
| **Debian** | `ready2rip_*-1_amd64.deb` / `_arm64.deb` | Ubuntu / Debian using distro GTK and encoders |
| **RPM** | `ready2rip-*-1.x86_64.rpm` / `.aarch64.rpm` | Fedora and related distributions |

### AppImage (recommended)

```bash
chmod +x ready2rip-0.4.45-x86_64.AppImage   # or …-aarch64.AppImage
./ready2rip-0.4.45-x86_64.AppImage
```

Requirements:

| Need | Notes |
|------|-------|
| Matching CPU arch | Separate builds for `x86_64` and `aarch64` |
| **glibc** ≥ build floor | Release images are built on Ubuntu 22.04 (glibc 2.35). glibc is **not** bundled — see [Portability](#appimage-portability) |
| Optical drive access | Your user should be in the `cdrom` group (or equivalent) |
| Working FUSE helper | `fuse3` / setuid `fusermount3` (see below). Prefer this over extract-and-run |
| Graphics stack | Mesa / Vulkan / libGL, Wayland or X11 |

For desktop icons, menus, and updates from GitHub, use
**[Gear Lever](https://github.com/mijorus/gearlever)**. Open the AppImage in
Gear Lever; ready2rip embeds update information at build time so it can detect
GitHub Releases when both the `.AppImage` and `.AppImage.zsync` assets are
published.

#### FUSE on modern Ubuntu / Debian

Current ready2rip AppImages use the AppImage **type2 runtime** with
**libfuse 3 statically linked**. They do **not** load host `libfuse.so.2`, so
installing `libfuse2` / `libfuse2t64` alone will not fix a mount failure.

What they need from the host:

1. The AppImage file must be executable (`chmod +x`), ideally under your home
   directory (some `~/Downloads` mounts are `noexec`).
2. Package **`fuse3`**, which provides setuid-root `/usr/bin/fusermount3`:

   ```bash
   sudo apt install fuse3
   ls -l /usr/bin/fusermount3   # expect -rwsr-xr-x root root
   ```

3. On Ubuntu **25.10+** (including 26.04 / 26.10), AppArmor’s `fusermount3`
   profile can still deny the mount with `Permission denied`. Confirm with:

   ```bash
   journalctl -k -g fusermount3 --since "5 min ago"
   ```

   Quick workarounds (pick one):

   ```bash
   # A) Run without mounting (always works; slower cold start)
   ./ready2rip-0.4.45-x86_64.AppImage --appimage-extract-and-run

   # B) Put only the fusermount3 profile into complain mode
   sudo apt install apparmor-utils
   sudo aa-complain fusermount3
   ```

If the AppImage window opens but the UI says the drive cannot be opened, that
is separate from FUSE — fix `cdrom` group membership / device path in Settings.

#### Verifying the AppImage signature

Release AppImages are GPG-signed. Import the project key from
[`packaging/ready2rip-signing-key.asc`](packaging/ready2rip-signing-key.asc)
(fingerprint `D0391EB1F5B61355C9532B26859C0EE899C00AE3`), then validate with the
`validate-*.AppImage` tools from the
[appimagetool continuous release](https://github.com/AppImage/appimagetool/releases).

### Debian / Ubuntu

```bash
sudo apt install ./ready2rip_0.4.45-1_amd64.deb   # or _arm64.deb
```

Depends on distro packages for GTK 4 / libadwaita, mutagen, Pillow, cdparanoia,
flac, lame, ffmpeg, and opus-tools. No FUSE/AppImage runtime is involved.

### Fedora / RPM

```bash
sudo dnf install ./ready2rip-0.4.45-1.x86_64.rpm   # or .aarch64.rpm
```

Same runtime idea as the `.deb`: system UI stack and encoders, ready2rip itself
from the package.

## Dependencies

### End users (AppImage)

Always required on the **host** (not shipped in the image):

| Requirement | Why |
|-------------|-----|
| Matching arch (`x86_64` or `aarch64`) | Native AppImage |
| **glibc** new enough for the build | See [Portability](#appimage-portability); `GLIBC_2.xx not found` means the image is newer than your distro |
| Optical drive + `cdrom` (or equivalent) | Read / eject the disc |
| **fuse3** (`fusermount3` setuid) | Mount the AppImage (preferred over `--appimage-extract-and-run`) |
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
| **Python ≥ 3.11** + **PyGObject** (Gtk 4, Adw 1) | UI |
| **cdparanoia** or **cd-paranoia** | TOC + secure extract |
| **mutagen** ≥ 1.47 | Tags + ReplayGain write |
| **Pillow** ≥ 10 | Cover-art JPEG conversion |
| **flac** | FLAC encode |
| **lame** | MP3 encode |
| **ffmpeg** | AAC (M4A), AIFF, ReplayGain analysis (`ebur128`), Opus fallback |
| **opusenc** | Opus encode (preferred over ffmpeg for Opus) |
| Meson, Ninja, gcc | Install / packaging |

Optional: **libfdk_aac** (in ffmpeg) for higher-quality AAC with VBR + afterburner;
**Wine + qaac.exe + Apple AAC DLLs** for Also-encode **QAAC** (set `READY2RIP_QAAC` or
install under `~/.wine/drive_c/qaac/`); **libdiscid** for DiscID helpers via ctypes
(pure-Python TOC IDs are used if missing).

```bash
# Fedora
sudo dnf install python3-gobject gtk4 libadwaita python3-mutagen python3-pillow \
  cdparanoia flac lame ffmpeg opus-tools

# Debian / Ubuntu
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 \
  python3-mutagen python3-pil cdparanoia flac lame ffmpeg opus-tools
```

For lint / unit tests from a checkout:

```bash
pip install --user 'mutagen>=1.47' 'pillow>=10' ruff pytest
ruff check src tests
PYTHONPATH=src pytest -q
```

### Build host (AppImage packaging)

Release CI builds on **Ubuntu 22.04** so the glibc floor stays at 2.35. A modern
GTK 4 / libadwaita stack is compiled into a cached GNOME prefix
(`appimage/build-gnome-prefix.sh`).

| Required on build host | Why |
|------------------------|-----|
| `python3` ≥ 3.11, PyGObject, GTK 4, libadwaita | Bundled into the image |
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

Native packages:

```bash
./packaging/build-deb-rpm.sh     # → dist/*.deb and dist/*.rpm
```

### AppImage portability

Bundled and largely independent of the host: Python + stdlib + PyGObject, the
ready2rip package, mutagen, Pillow, GTK 4 / Adwaita (from the GNOME prefix), and
cdparanoia. Encoders are included only when the **build host** had them.

What is **not** fully portable:

| Constraint | Why |
|------------|-----|
| **glibc version** | Linux cannot safely ship its own glibc in an AppImage. Release AppImages are built on **Ubuntu 22.04** (glibc 2.35) with a cached GNOME prefix for modern GTK 4 / libadwaita. Hosts older than that glibc floor will fail with `GLIBC_2.xx not found`. |
| **FUSE helper / AppArmor** | Runtime mounts via host `fusermount3`; see [FUSE on modern Ubuntu / Debian](#fuse-on-modern-ubuntu--debian) |
| **GPU / display stack** | Mesa, libGL, Vulkan, Wayland/X11 client libs come from the host |
| **Architecture** | Separate AppImages for `x86_64` and `aarch64` |

Check your system glibc with `ldd --version`. If the AppImage fails with
`GLIBC_2.xx not found`, rebuild on an older base or run on a newer distro.

## Develop from source

GSettings schemas compile into the user cache on first launch. From the repo
(Python ≥ 3.11, system GTK 4 / libadwaita, and the packages above):

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
  packaging/            # deb/rpm (nfpm)
  data/                 # desktop, icons, AppStream, GSettings
  docs/screenshots/     # release / docs images
  src/ready2rip/
  tests/
  meson.build
```

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).
