# ready2rip

ready2rip is a secure CD ripper for Linux. It extracts with full cdparanoia, checks the result against AccurateRip, and tags the files from MusicBrainz or gnudb, along with any CD-TEXT, MCN, and ISRC the drive can read.


<img width="1050" height="750" alt="Screenshot From 2026-11-24 17-14-22" src="https://github.com/user-attachments/assets/3d333aec-1b46-40ed-8631-5802917037b7" />


## Install

Builds for the current release are on the [Releases](https://github.com/gnostiko/ready2rip/releases) page. 0.4.76 ships an AppImage, a deb, and an rpm, each for x86_64 and aarch64.

### AppImage

```bash
chmod +x ready2rip-0.4.76-x86_64.AppImage   # or …-aarch64.AppImage
./ready2rip-0.4.76-x86_64.AppImage
```

You need a matching CPU arch, glibc 2.43 or newer (the image is built on Ubuntu 26.04), permission to use the optical drive (usually the `cdrom` group), FUSE (`fusermount3`), and a normal graphics stack. [Gear Lever](https://github.com/mijorus/gearlever) can add a menu icon and updates if you want that. On older hosts, use the deb or rpm, an earlier AppImage, or run from source.

On Debian or Ubuntu:

```bash
sudo apt install fuse3
```

If the image fails to mount with `Permission denied` on Ubuntu 25.10 or later, try:

```bash
./ready2rip-0.4.76-x86_64.AppImage --appimage-extract-and-run
```

Or, if AppArmor is blocking the FUSE helper: `sudo aa-complain fusermount3`.

### Debian / Ubuntu

```bash
sudo apt install ./ready2rip_0.4.76-1_amd64.deb   # or _arm64.deb
```

### Fedora / RPM

```bash
sudo dnf install ./ready2rip-0.4.76-1.x86_64.rpm   # or .aarch64.rpm
```

## From source

You need Python 3.11 or newer, PyGObject with GTK 4 and libadwaita, mutagen, and Pillow. For the disc, ready2rip looks for `cd-paranoia` first (from libcdio-paranoia) and falls back to classic `cdparanoia`. Encoding uses `flac`, `lame`, `ffmpeg`, and `opusenc`. ffmpeg can also handle Opus, WavPack, ALAC, and AIFF if the dedicated tools are missing. WAV is written directly and does not need an encoder. Meson, Ninja, and a C compiler are only required if you want to install or package it.

```bash
# Fedora
sudo dnf install python3-gobject gtk4 libadwaita python3-mutagen python3-pillow \
  libcdio-paranoia flac lame ffmpeg opus-tools

# Debian / Ubuntu
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 \
  python3-mutagen python3-pil cd-paranoia flac lame ffmpeg opus-tools
```

Add `meson`, `ninja`, and a C compiler if you plan to install with Meson. `wavpack` is optional; ffmpeg can encode WavPack without it.

From the repo root:

```bash
./run
```

That starts the GTK app from the source tree. No Meson build is required. To run the tests:

```bash
pip install --user 'mutagen>=1.47' 'pillow>=10' ruff pytest
ruff check src tests
PYTHONPATH=src pytest -q
```

To install system-wide, or to build packages:

```bash
meson setup build && meson compile -C build
sudo meson install -C build
```

```bash
./appimage/build-appimage.sh          # -> dist/*.AppImage
./packaging/build-deb-rpm.sh          # -> dist/*.deb / *.rpm
```

After a system install the command is `ready2rip`. There are no extra CLI flags; this is a desktop app.

## Usage

Insert an audio CD. ready2rip should notice it and list the tracks. The first time (or when you switch drives) it will offer to measure the sample offset. AccurateRip is on by default, and in that case a rip will not start until the offset is set. You can run that from Settings later, or enter a value from [accuraterip.com/driveoffsets.htm](http://accuraterip.com/driveoffsets.htm).

Metadata is looked up automatically unless you turn that off. If it picks the wrong release, use Lookup, search by disc ID, or paste a MusicBrainz release URL. Album and track fields, and the cover, can all be edited.

Choose a format and rip mode in Settings, then press Rip (Ctrl+R). Files go under your output folder, which defaults to the XDG Music directory, usually `~/Music`. The default names look like:

```
{album_artist}/{dated_album}/{disc_folder}/{track:02d} - {title}
```

`{disc_folder}` is only added for multi-disc albums (CD1, CD2, and so on). Presets and a custom template are in Settings, along with how multiple artists are written (`semicolon`, `slash`, or `feat` in the artist field).

## Settings

The primary rip is always lossless: FLAC (default, compression level 5), WAV, AIFF, WavPack, or ALAC. You can also encode a lossy copy of the same rip: LAME MP3 (default V0), FDK AAC (default 256 kbps CBR), Opus (default 160 kbps VBR), or QAAC. QAAC is Apple AAC via Wine; it is found on `PATH`, under Wine, or by pointing `READY2RIP_QAAC` at the binary. The default QAAC preset is CVBR 256.

Rip mode defaults to Archival: secure paranoia with retries, plus a test-and-copy CRC check. Normal is a single paranoia pass with repair. Burst is `cdparanoia -Z` (paranoia off), which is faster but not archival. Copy image, off by default, writes one continuous lossless file instead of per-track files. AccurateRip still checks each track in that mode, and the lossy "also encode" step is skipped. If you somehow still have a lossy format selected for the image, it falls back to FLAC.

Hidden Track One Audio (a longer, non-silent track-1 pregap, saved as track 00) and mixed-mode or CD-Extra data tracks (raw `.bin`) are on. A rip log and a CUE sheet are written in the album folder. AccurateRip verification is on. Strict archival (fail the rip on database mismatches) and burst fallback after secure retries fail are both off. Auto-rip and auto-eject are off. If a destination already exists, the default is to overwrite it; you can switch that to Ask, Skip existing, or Keep both.

MusicBrainz and gnudb lookups are on. ReplayGain is written by default, and iTunes Sound Check is written on MP3, M4A, AIFF, and WAV. Cover art comes from the Cover Art Archive and iTunes. It is embedded in lossy files at 600 px by default; lossless embed is off unless you turn it on, and `cover.jpg` in the album folder stays full resolution. Lyrics from LRCLIB are off until you enable them, and then they are only saved with lossless rips (tags, `.lrc`/`.txt` sidecars, or both).

The optical device defaults to `/dev/sr0`.

## Notes

If the app cannot see a disc, check that `cd-paranoia` or `cdparanoia` is installed and that your user can access the drive (typically the `cdrom` group). A missing encoder only matters for the format you actually picked.

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).
