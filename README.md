# ready2rip

**ready2rip is a secure CD ripper for Linux.**

Careful archival extraction with full cdparanoia, AccurateRip and CTDB
verification, MusicBrainz / gnudb metadata, disc-native CD-TEXT / MCN / ISRC,
and modern tagging.

<img width="1050" height="750" alt="Screenshot From 2026-11-14 14-51-13" src="https://github.com/user-attachments/assets/bebff08d-c118-42db-98cf-7e15f92cf286" />


## What it does

| Feature | Description |
|---------|-------------|
| Secure rip | Full cdparanoia, retries, test & copy (CRC match required) |
| AccurateRip | Online CRC check after extraction (per-track and Copy image) |
| CTDB | CUETools Database verify; optional confirm / new-parity submit |
| Metadata | MusicBrainz + FreeDB-compatible lookup; editable album and tracks |
| Disc extras | Live CD-TEXT, MCN (barcode), and per-track ISRC when the drive supports it |
| Tags | mutagen — FLAC / MP3 / AAC (M4A) / Opus / WAV / AIFF |
| ReplayGain | Track + album loudness tags; optional iTunes Sound Check |
| Artwork | Cover Art Archive and iTunes, with optional embed sizes |
| Lyrics | Optional LRCLIB fetch; embed and/or sidecar for lossless |
| Logs & CUE | Detailed rip log; multi-file or image CUE |
| Drive setup | Sample offset calibration (required when AccurateRip or CTDB is on) |

## Typical workflow

1. **Install / run** — AppImage recommended (see [Install](#install)). [Gear Lever](https://github.com/mijorus/gearlever) adds menu integration and updates.
2. **Insert an audio CD.** ready2rip detects the disc and shows tracks.
3. **Drive setup** (first run, or new drive): calibrate **sample offset**. With AccurateRip or CTDB on, ripping is blocked until calibrated.
4. **Metadata** (optional): auto-lookup, or **Lookup → By disc ID** / paste a MusicBrainz release URL. Edit fields and cover art as needed.
5. **Settings**: encoder, rip mode (Archival / Normal / Burst), CUE/log, Copy image, HTOA, Existing files, and related options.
6. Press **Rip**. Files land under your output folder using the album/track templates.

### Secure rip (summary)

| Step | Behaviour |
|------|-----------|
| Full paranoia | Overlap / jitter correction and multi-read repair |
| Never-skip | Keep re-reading imperfect data; don’t silently leave holes |
| Sample offset | Applied at extract time when calibrated |
| Test and copy | Extract twice, compare CRC32; unmatched tracks fail the rip |
| AccurateRip / CTDB | Verify offset-corrected audio against public databases |
| Burst fallback | Off by default — optional insecure recovery after secure retries fail |

**Copy image** rips one continuous lossless image (FLAC / WAV / AIFF) instead of per-track files; AR and CTDB still verify per track. Mixed-mode data tracks are saved as raw `.bin` beside the lossless output.

Default layout: `{album_artist}/{dated_album}/{disc_folder}` / `{track:02d} - {title}`.

## Options

### Paths and naming

| Option | Default / notes |
|--------|-----------------|
| **Optical device** | Usually `/dev/sr0` |
| **Output folder** | XDG Music when unset |
| **Existing files** | **Overwrite** (default), **Ask**, **Skip existing**, or **Keep both** |
| **Album / track schemes** | Presets or Custom (hover a preset for the template) |
| **Artist style** | `semicolon`, `slash`, or `feat` in the title |

### Encoding

| Option | Notes |
|--------|-------|
| **Primary format** | WAV, AIFF, or FLAC (default **FLAC**) |
| **Also encode** | Optional LAME MP3, FDK AAC, QAAC, or Opus |
| **FLAC compression** | 0 (Fastest) … 8 (Smallest); default **5 (Default)** |
| **Lossy quality** | MP3 V0, AAC 256 CBR, Opus 160 kbps VBR by default |

### Extraction

| Option | Default | Notes |
|--------|---------|-------|
| **Rip mode** | Archival | Archival = secure + test & copy; Normal = paranoia with repair; Burst = `-Z` |
| **Copy image** | Off | One continuous image; AR + CTDB still verify per track |
| **Pregap / HTOA** | On | Longer non-silent track-1 pregap → track `00` |
| **Data tracks** | On | Mixed-mode / CD-Extra as raw `.bin` |
| **AccurateRip / CTDB** | On | Require calibrated sample offset |
| **Strict archival** | Off | Fail archival on DB mismatches; off keeps Secure OK with partial AR notes |
| **Submit to CTDB** | Off | Confirm match or submit new parity |
| **Burst fallback** | Off | Insecure `-Z` after Archival retries fail |
| **Write rip log / CUE** | On | Log and CUE in the album folder |
| **Auto-rip / Auto-eject** | Off | Start after detect / eject when a rip finishes |

### Metadata & art

Auto-lookup, MusicBrainz / FreeDB, disc gap-fill (CD-TEXT / MCN / ISRC), artwork download and embed, ReplayGain, optional lyrics, Encoder (codec tool) and Encoded by (`ready2rip <version>`).

## Install

### AppImage

```bash
chmod +x ready2rip-0.4.71-x86_64.AppImage   # or …-aarch64.AppImage
./ready2rip-0.4.71-x86_64.AppImage
```

Needs a matching CPU arch, **glibc ≥ 2.43** (built on Ubuntu 26.04), `cdrom` group access, **fuse3** (`fusermount3`), and a normal graphics stack. Use [Gear Lever](https://github.com/mijorus/gearlever) for menu icons and updates. Older hosts should use the deb/rpm packages or a prior AppImage release.

```bash
sudo apt install fuse3
```

If mount fails with `Permission denied` on Ubuntu 25.10+:

```bash
./ready2rip-0.4.71-x86_64.AppImage --appimage-extract-and-run
# or: sudo aa-complain fusermount3
```

### Debian / Ubuntu

```bash
sudo apt install ./ready2rip_0.4.71-1_amd64.deb   # or _arm64.deb
```

### Fedora / RPM

```bash
sudo dnf install ./ready2rip-0.4.71-1.x86_64.rpm   # or .aarch64.rpm
```

## Develop from source

| Dependency | Role |
|------------|------|
| **Python ≥ 3.11** + **PyGObject** (Gtk 4, Adw 1) | UI |
| **cdparanoia** | TOC + secure extract |
| **mutagen**, **Pillow** | Tags and cover art |
| **flac**, **lame**, **ffmpeg**, **opusenc** | Encode |
| Meson, Ninja | Install / packaging |

```bash
# Fedora
sudo dnf install python3-gobject gtk4 libadwaita python3-mutagen python3-pillow \
  cdparanoia flac lame ffmpeg opus-tools

# Debian / Ubuntu
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 \
  python3-mutagen python3-pil cdparanoia flac lame ffmpeg opus-tools

./run
pip install --user 'mutagen>=1.47' 'pillow>=10' ruff pytest
ruff check src tests
PYTHONPATH=src pytest -q
```

```bash
meson setup build && meson compile -C build
./appimage/build-appimage.sh          # → dist/*.AppImage
./packaging/build-deb-rpm.sh          # → dist/*.deb / *.rpm
```

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).
