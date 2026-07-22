# ready2rip

A personal vibe-coded project; I love dBpoweramp and EAC, but since I use linux primarily it can be a little tedious dealing with these programs under Wine. Decided to experiement with AI to build a ripper for my personal workflow and this is what I've come up with; a secure CD ripper styled for the GNOME DE.

<img width="2180" height="1482" alt="Screenshot From 2026-07-21 08-28-31" src="https://github.com/user-attachments/assets/ca236dff-7f8f-4b4b-89ca-8be169fcfa54" />


## What it does

| Feature | Description |
|---------|-------------|
| Secure rip | Full **cdparanoia**, retries, optional test & copy |
| AccurateRip | Online CRC database check after extraction |
| Metadata | MusicBrainz + FreeDB-compatible (e.g. gnudb) |
| Tags | mutagen — FLAC / MP3 / Opus / WAV |
| ReplayGain | Track + album loudness tags |
| Artwork | Cover Art Archive, Deezer and iTunes; optional embed sizes |
| Logs & CUE | EAC-style rip log and multi-file / image CUE sheets |
| Drive setup | Sample offset, Accurate Stream, cache, C2 probe |

---

## Typical workflow

1. **Install / run** the AppImage (recommended: with [Gear Lever](https://flathub.org/apps/it.mijorus.gearlever) - see [Run the AppImage](#run-the-appimage)).
2. **Insert an audio CD.** ready2rip detects the disc, reads the TOC, and shows tracks.
3. **Drive setup** (first run, or when you change drives): calibrate **sample offset**, measure Accurate Stream / cache / C2 support.
4. **Metadata** (optional): auto-lookup when a disc is detected, or press **Lookup** and pick a release. Edit album/track fields if needed. Cover art can be fetched, chosen from disk, or cleared.
5. **Rip options** (sidebar): choose encoder, test & copy, CUE/log, Copy Image, HTOA, etc. Defaults already match a secure archival setup.
6. Press **Rip CD**. Progress appears in the bottom panel (status line shows track detail).
7. Files land under your **output folder** using the album/track templates. Open the folder to find audio, optional `cover`, **`.log`**, and **`.cue`**.

### What a secure rip does (under the hood)

ready2rip aims for **EAC-like secure behaviour** on Linux via **cdparanoia** / libcdio-paranoia:

| Step | Behaviour |
|------|-----------|
| Full paranoia | Overlap / jitter correction and multi-read repair (not burst mode by default) |
| Never-skip + abort-on-skip | `--never-skip=200` and `-X` — keep re-reading imperfect data; don’t silently leave holes |
| Sample offset | Applied at extract time (`-O`) when calibrated — same idea as EAC “read offset correction” |
| Test and copy | Extract twice, compare CRC32; retry on mismatch; defeat drive audio cache between passes when needed |
| AccurateRip | Verifies the offset-corrected audio against the public AR database |
| Error logging | Parses cdparanoia progress into quality / fixups / skips / suspicious positions (EAC-style log) |
| Burst fallback | Only if secure extract fails and the option is on — paranoia off (`-Z`), noted in the log |

**Copy Image** mode rips one continuous disc image (FLAC/WAV) instead of per-track files; enable **Write .cue file** for a matching image CUE. Per-track rips use a multi-file CUE (“left-out gaps”) when that option is on.

---

## Options (Rip options sidebar)

### Paths and naming

| Option | Default / notes |
|--------|------------------|
| **Optical device** | Usually `/dev/sr0` |
| **Output folder** | Empty → XDG Music (`~/Music`) |
| **Album folder template** | `{album_artist}/{album}/{disc_folder}` — also `{year}`, `{disc}`, `{totaldiscs}` |
| **Track filename template** | `{track:02d} - {title}` — also `{artist}`, `{album}`, `{disc}`, `{totaldiscs}` |

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
| **Pregap / HTOA** | On | EAC-style: ignore ≤2s track-1 pause; longer non-silent pregap → track `00`; track 1 starts at index 01 |
| **AccurateRip** | On | Online CRC verification |
| **Burst fallback** | On | Last resort if secure rip fails |
| **Write rip log** | On | EAC-style status log in the album folder |
| **Write .cue file** | On | Multi-file CUE, or image CUE when Copy Image is on |
| **Auto-rip** | Off | Start rip shortly after a new disc is detected |
| **Auto-eject** | Off | Open tray after a successful rip |

### Drive / calibration

| Option | Notes |
|--------|--------|
| **Sample offset** | From Drive setup or [driveoffsets.htm](http://www.accuraterip.com/driveoffsets.htm) |
| **Drive setup** | Offset scan, Accurate Stream, audio cache, C2 capability |

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

For the **released AppImage**, most of the stack is **bundled** (Python/PyGObject, GTK/Adwaita libraries best-effort, mutagen, cdparanoia, encoders present at build time). You mainly need:

- A recent **x86_64** Linux desktop
- **Optical drive** access (user in `cdrom` / appropriate group)
- **FUSE / libfuse2** recommended so the AppImage mounts quickly (avoid `APPIMAGE_EXTRACT_AND_RUN=1` for daily use)

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
| `python3`, PyGObject, GTK 4, libadwaita | Bundled into the image |
| `cdparanoia` / `cd-paranoia` | Bundled — build fails if missing |
| `meson`, `ninja`, `gcc`, `curl`, `pip` | Packaging |
| `flac`, `lame`, `ffmpeg` | Bundled if present |
| `zsync` / **zsyncmake** | Recommended — produces `.zsync` for Gear Lever delta updates |

---

## Run the AppImage

### Download and run

1. Get the latest **`ready2rip-*-x86_64.AppImage`** from [GitHub Releases](https://github.com/gnostiko/ready2rip/releases).
2. Make it executable and start it:

```bash
chmod +x ready2rip-0.2.0-x86_64.AppImage
./ready2rip-0.2.0-x86_64.AppImage
```

### Recommended: manage with Gear Lever

For desktop integration (icons, menus) and **updates from GitHub**, use **[Gear Lever](https://flathub.org/apps/it.mijorus.gearlever)**:

```bash
flatpak install flathub it.mijorus.gearlever
```

Open the AppImage with Gear Lever (or add it from Gear Lever’s UI). ready2rip embeds AppImage update information at build time so Gear Lever can detect **GitHub Releases** automatically when both the `.AppImage` and `.AppImage.zsync` assets are published.

**Manual Gear Lever update settings** (if needed):

| Field | Value |
|-------|--------|
| Provider | Github |
| Username/Repo | `gnostiko/ready2rip` |
| Release file name | `ready2rip-*-x86_64.AppImage` |

Embedded update string (for reference):

```text
gh-releases-zsync|gnostiko|ready2rip|latest|ready2rip-*-x86_64.AppImage.zsync
```

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
- [Exact Audio Copy](https://www.exactaudiocopy.de/)
- [fre:ac](https://www.freac.org/)
- [ABCDE](https://abcde.einval.com/)
- [Whipper](https://github.com/whipper-team/whipper)
- [cyanrip](https://github.com/cyanreg/cyanrip)

Thanks to those projects and their communities for defining what careful, accurate CD ripping looks like on every platform.

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).

---

