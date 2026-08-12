# ready2rip

**ready2rip is a secure CD ripper for Linux.**

A CD ripper focused on careful archival extraction with full cdparanoia,
AccurateRip verification, modern metadata, and various tag options, utilizing GNOME design.

<img width="2100" height="1500" alt="Screenshot From 2026-08-11 19-34-48" src="https://github.com/user-attachments/assets/7f924b51-35e3-41d5-9fa0-415cb1b5c902" />
<img width="2100" height="1500" alt="Screenshot From 2026-08-11 19-33-01" src="https://github.com/user-attachments/assets/e98ef01c-eebc-4c1b-99ae-830bcf459eae" />
<img width="2100" height="1500" alt="Screenshot From 2026-08-11 19-28-47" src="https://github.com/user-attachments/assets/ea8eab3c-c61a-4028-9ec6-448432dcdd55" />


## What it does

| Feature | Description |
|---------|-------------|
| Secure rip | Full cdparanoia, retries, optional test & copy |
| AccurateRip | Online CRC database check after extraction |
| Metadata | MusicBrainz + FreeDB-compatible (e.g. gnudb) |
| Tags | mutagen — FLAC / MP3 / Opus / WAV |
| ReplayGain | Track + album loudness tags |
| Artwork | Cover Art Archive, Deezer and iTunes; optional embed sizes |
| Logs & CUE | Detailed rip log and multi-file / image CUE sheets |
| Drive setup | Sample offset, Accurate Stream, cache probe |

---

## Typical workflow

1. **Install / run** the AppImage (recommended: with [Gear Lever](https://flathub.org/apps/it.mijorus.gearlever) - see [Run the AppImage](#run-the-appimage)).
2. **Insert an audio CD.** ready2rip detects the disc, reads the TOC, and shows tracks.
3. **Drive setup** (first run, or when you change drives): calibrate **sample offset**, measure Accurate Stream / cache support.
4. **Metadata** (optional): auto-lookup when a disc is detected, or open **Lookup** → **By disc ID** and pick a release. If the wrong album is matched, use **Lookup** → **MusicBrainz link…** and paste a `musicbrainz.org/release/…` URL (or release UUID). Edit album/track fields if needed. Cover art can be fetched, chosen from disk, or cleared.
5. **Rip options** (sidebar): choose encoder, test & copy, CUE/log, Copy Image, HTOA, etc. Defaults already match a secure archival setup.
6. Press **Rip CD**. Progress appears in the bottom panel (status line shows track detail).
7. Files land under your **output folder** using the album/track templates. Open the folder to find audio, optional `cover`, **`.log`**, and **`.cue`**.

### What a secure rip does (under the hood)

ready2rip aims for secure archival ripping on Linux via cdparanoia / libcdio-paranoia:

| Step | Behaviour |
|------|-----------|
| Full paranoia | Overlap / jitter correction and multi-read repair (not burst mode by default) |
| Never-skip + abort-on-skip | `--never-skip=200` and `-X` — keep re-reading imperfect data; don’t silently leave holes |
| Sample offset | Applied at extract time (`-O`) when calibrated (read offset correction) |
| Test and copy | Extract twice, compare CRC32; retry on mismatch; defeat drive audio cache between passes when needed |
| AccurateRip | Verifies the offset-corrected audio against the public AR database |
| Error logging | Parses cdparanoia progress into quality / fixups / skips / suspicious positions |
| Burst fallback | Only if secure extract fails and the option is on — paranoia off (`-Z`), noted in the log |

**Copy Image** mode rips one continuous disc image (FLAC/WAV) instead of per-track files; enable **Write .cue file** for a matching image CUE. Per-track rips use a multi-file CUE (“left-out gaps”) when that option is on.

---

## Options (Rip options sidebar)

### Paths and naming

| Option | Default / notes |
|--------|------------------|
| **Optical device** | Usually `/dev/sr0` |
| **Output folder** | (`~/Music`) |
| **Album folder template** | `{album_artist}/{album}/{disc_folder}` - also `{year}`, `{disc}`, `{totaldiscs}` |
| **Track filename template** | `{track:02d} - {title}` - also `{artist}`, `{album}`, `{disc}`, `{totaldiscs}` |

### Encoder

| Option | Notes |
|--------|--------|
| **Encoder** | FLAC, MP3, Opus, or WAV |
| **FLAC compression** | 0 (fast) … 8 (smallest); default 5 |
| **MP3 bitrate** | CBR (e.g. 320 kbps) |
| **Opus bitrate** | kbps (typical 96–256) |

Copy Image forces a lossless container (FLAC or WAV; other formats fall back to FLAC).

### Extraction

| Option | Default | Notes |
|--------|---------|--------|
| **Test and copy** | On | Two secure passes; matching CRCs required |
| **Copy Image** | Off | One continuous image instead of separate tracks |
| **Pregap / HTOA** | On | Ignore ≤2s track-1 pause; longer non-silent pregap → track `00`; track 1 starts at index 01 |
| **AccurateRip** | On | Online CRC verification |
| **Burst fallback** | On | Last resort if secure rip fails |
| **Write rip log** | On | Detailed status log in the album folder |
| **Write .cue file** | On | Multi-file CUE, or image CUE when Copy Image is on |
| **Auto-rip** | Off | Start rip shortly after a new disc is detected |
| **Auto-eject** | Off | Open tray after a successful rip |

### Drive / calibration

| Option | Notes |
|--------|--------|
| **Sample offset** | From Drive setup or [driveoffsets.htm](http://www.accuraterip.com/driveoffsets.htm) |
| **Drive setup** | Offset scan, Accurate Stream, audio cache |

### Metadata & art

| Option | Notes |
|--------|--------|
| **Look up automatically** | Query when a disc is detected |
| **MusicBrainz / FreeDB** | Sources for lookup |
| **Download artwork** | iTunes, Cover Art Archive, Deezer |
| **Embed artwork** | Write cover into audio files; max embed edge size |
| **ReplayGain** | Track + album tags after the rip set is complete |

---

## Dependencies

### End users (AppImage)

For the **released AppImage**, the runtime is **self-contained** as far as packaging allows:

- Bundled **Python interpreter + stdlib** (does not use host Python)
- Bundled **PyGObject**, **GTK 4 / libadwaita** (and linked libraries), typelibs, GSettings schemas
- Bundled **cdparanoia**, **flac** / **lame** / **ffmpeg** / **opusenc** when present on the build host
- Bundled **mutagen**

You mainly need:

- **x86_64** Linux desktop with a **glibc new enough for the build host** (see [Portability](#appimage-portability) below)
- **Optical drive** access (user in `cdrom` / appropriate group)
- **FUSE / libfuse2** recommended so the AppImage mounts quickly (avoid `APPIMAGE_EXTRACT_AND_RUN=1` for daily use)
- Host **Mesa / Vulkan / libGL** (graphics drivers stay on the system — normal for AppImages)

### Develop / run from source

| Dependency | Role |
|------------|------|
| Python 3 + **PyGObject** (Gtk 4, Adw 1) | UI |
| **cdparanoia** or **cd-paranoia** | TOC + secure extract |
| **mutagen** | Tags / ReplayGain writing |
| **flac**, **lame**, **ffmpeg** / **opusenc** | Encoding (and RG analysis where needed) |
| Meson, Ninja, gcc | Build / AppImage packaging |

```bash
pip3 install --user mutagen
```

Optional: **libdiscid** (ctypes) for DiscID helpers; pure-Python TOC IDs are used if it is missing.

### Build host (AppImage packaging)

| Required on build host | Why |
|------------------------|-----|
| `python3`, PyGObject, GTK 4, libadwaita | Bundled into the image (incl. Python stdlib) |
| `cdparanoia` / `cd-paranoia` | Bundled - build fails if missing |
| `meson`, `ninja`, `gcc`, `curl`, `pip` | Packaging |
| `flac`, `lame`, `ffmpeg` | Bundled if present |
| `zsync` / **zsyncmake** | Recommended - produces `.zsync` for Gear Lever delta updates |

```bash
./appimage/build-appimage.sh
# → dist/ready2rip-VERSION-x86_64.AppImage
```

The script runs an **isolated smoke-test** (no host Python modules) before packing. It also prints the **glibc symbol floor** of the bundled libraries.

### AppImage portability

What is portable:

| Bundled | Independent of host? |
|---------|----------------------|
| Python + stdlib + PyGObject | Yes |
| ready2rip package, mutagen | Yes |
| GTK 4 / Adwaita + most deps | Yes (best-effort) |
| cdparanoia + encoders | Yes (if present at build time) |

What is **not** fully portable:

| Constraint | Why |
|------------|-----|
| **glibc version** | Linux cannot safely ship its own glibc in an AppImage. The image needs a host glibc **≥ the newest symbol used by bundled libs**. A build on a bleeding-edge distro (e.g. Solus with glibc 2.43) will **not** run on older Ubuntu/Debian. Prefer building releases on the **oldest supported target** (or a container of that vintage) when you need wide reach. |
| **GPU / display stack** | Mesa, libGL, Vulkan, Wayland/X11 client libs come from the host. |
| **Architecture** | x86_64 only (script enforces this). |

Check your system glibc with `ldd --version`. If the AppImage fails with `GLIBC_2.xx not found`, rebuild on an older base or run on a newer distro.

---

## Run the AppImage

### Download and run

1. Get the latest **`ready2rip-*-x86_64.AppImage`** from [GitHub Releases](https://github.com/gnostiko/ready2rip/releases).
2. Make it executable and start it:

```bash
chmod +x ready2rip-0.4.0-x86_64.AppImage
./ready2rip-0.4.0-x86_64.AppImage
```

### Recommended: manage with Gear Lever

For desktop integration (icons, menus) and **updates from GitHub**, use **[Gear Lever](https://flathub.org/apps/it.mijorus.gearlever)**:

```bash
flatpak install flathub it.mijorus.gearlever
```

Open the AppImage with Gear Lever (or add it from Gear Lever’s UI). ready2rip embeds AppImage update information at build time so Gear Lever can detect **GitHub Releases** automatically when both the `.AppImage` and `.AppImage.zsync` assets are published.


---

## Develop from source

### Run without installing

```bash
cd "/path/to/ready2rip"
meson setup build
cp data/org.ready2rip.Ready2Rip.gschema.xml build/data/
glib-compile-schemas build/data
PYTHONPATH=src GSETTINGS_SCHEMA_DIR=build/data python3 -m ready2rip.main
```

### Install with Meson

```bash
meson setup build
meson compile -C build
meson setup build --prefix="$PWD/install" --reconfigure
meson install -C build
./install/bin/ready2rip
```

### Project layout

```text
ready2rip/
  appimage/             # AppRun + build-appimage.sh
  data/                 # desktop, icons, AppStream, GSettings
  po/
  src/ready2rip/
  meson.build
```


---

## Inspired by

- [dBpoweramp](https://www.dbpoweramp.com/)
- [fre:ac](https://www.freac.org/)
- [ABCDE](https://abcde.einval.com/)
- [Whipper](https://github.com/whipper-team/whipper)
- [cyanrip](https://github.com/cyanreg/cyanrip)

Thanks to those projects and their communities for defining what careful, accurate CD ripping looks like on every platform.

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).

---

