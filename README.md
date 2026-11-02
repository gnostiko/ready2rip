# ready2rip

Secure CD ripper for Linux — cdparanoia extraction, AccurateRip + CTDB verification, MusicBrainz metadata, and modern tagging in a GTK 4 / libadwaita UI.

<img width="1024" alt="Main window" src="docs/screenshots/01-main-window.png" />
<img width="1024" alt="Choose release" src="docs/screenshots/02-choose-release.png" />
<img width="1024" alt="Ripping in progress" src="docs/screenshots/03-ripping.png" />

## Features

| Area | What you get |
|------|----------------|
| Secure rip | Full cdparanoia, retries, optional test & copy |
| AccurateRip | Online CRC check after extraction |
| CTDB | CUETools Database verify; optional confirm / new-parity submit |
| Metadata | MusicBrainz + FreeDB-compatible (e.g. gnudb); editable album/tracks |
| Encode / tags | FLAC, MP3, AAC (M4A), Opus, WAV via mutagen + ReplayGain |
| Artwork | Cover Art Archive / iTunes; optional embed sizes |
| Logs & CUE | Rip log; multi-file or image CUE (INDEX 00 pregaps, mixed-mode data) |
| Drive setup | Sample offset calibration |

## Downloads

Assets ship on every [GitHub Release](https://github.com/gnostiko/ready2rip/releases):

| Format | Architectures | File pattern | Notes |
|--------|---------------|--------------|-------|
| **AppImage** | `x86_64`, `aarch64` | `ready2rip-*-<arch>.AppImage` (+ `.zsync`) | Portable; GPG-signed; best default |
| **Debian** | `amd64`, `arm64` | `ready2rip_*-1_<arch>.deb` | Uses distro GTK / encoders |
| **RPM** | `x86_64`, `aarch64` | `ready2rip-*-1.<arch>.rpm` | Fedora / RHEL-family |
| **Flatpak** | `x86_64`, `aarch64` | `ready2rip-*-<arch>.flatpak` | Bundled runtime + tools |

### AppImage

```bash
chmod +x ready2rip-0.4.11-x86_64.AppImage   # or …-aarch64.AppImage
./ready2rip-0.4.11-x86_64.AppImage
```

| Host need | Why |
|-----------|-----|
| Matching CPU arch | Image is native `x86_64` or `aarch64` |
| New enough **glibc** | glibc is not bundled; see build log for minimum |
| **FUSE** (`libfuse2` / `fuse`) | Preferred mount path |
| Optical drive + `cdrom` group | Read / eject |
| GPU / display stack | Mesa / Wayland / X11 from the host |

For icons, menus, and GitHub update checks, use **[Gear Lever](https://flathub.org/apps/it.mijorus.gearlever)**:

```bash
flatpak install flathub it.mijorus.gearlever
```

#### Verify AppImage signature

Releases are signed with the project key in [`packaging/ready2rip-signing-key.asc`](packaging/ready2rip-signing-key.asc)
(`D0391EB1F5B61355C9532B26859C0EE899C00AE3`).

```bash
gpg --import packaging/ready2rip-signing-key.asc
# validate with AppImage's validate-x86_64.AppImage / validate-aarch64.AppImage
# from https://github.com/AppImage/appimagetool/releases (continuous)
```

### Debian / Ubuntu (`.deb`)

```bash
sudo apt install ./ready2rip_0.4.11-1_amd64.deb   # or _arm64.deb
```

| Package depends on | Role |
|--------------------|------|
| `python3`, `python3-gi`, `gir1.2-gtk-4.0`, `gir1.2-adw-1` | UI |
| `python3-mutagen`, `python3-pil` | Tags / cover conversion |
| `cdparanoia`, `flac`, `lame`, `ffmpeg`, `opus-tools` | Extract + encode |

### Fedora / RPM

```bash
sudo dnf install ./ready2rip-0.4.11-1.x86_64.rpm   # or .aarch64.rpm
```

| Package depends on | Role |
|--------------------|------|
| `python3`, `python3-gobject`, `gtk4`, `libadwaita` | UI |
| `python3-mutagen`, `python3-pillow` | Tags / cover conversion |
| `cdparanoia`, `flac`, `lame`, `ffmpeg`, `opus-tools` | Extract + encode |

### Flatpak (`.flatpak` bundle)

```bash
flatpak install --user ./ready2rip-0.4.11-x86_64.flatpak
flatpak run org.ready2rip.Ready2Rip
```

| Bundled / extended | Role |
|--------------------|------|
| GNOME Platform 49 | GTK 4 / libadwaita runtime |
| `libcdio-paranoia`, `flac`, `lame` | Extract + lossless/MP3 encode |
| `org.freedesktop.Platform.ffmpeg-full` | AAC / Opus via ffmpeg |
| mutagen, Pillow | Tags / artwork |

## Workflow

1. Insert an audio CD — TOC and tracks appear.
2. Calibrate **sample offset** in Drive setup when you first use a drive.
3. **Lookup** metadata (auto or by disc ID / MusicBrainz link); edit fields and art as needed.
4. Choose encoder and rip options in Settings (test & copy, CUE/log, Copy Image, HTOA, …).
5. **Rip CD** — progress shows in the bottom banner; files land under your output folder templates.

### Secure rip (under the hood)

| Step | Behaviour |
|------|-----------|
| Full paranoia | Overlap / jitter correction and multi-read repair (not burst by default) |
| Never-skip + abort-on-skip | Keep re-reading imperfect data; don’t silently leave holes |
| Sample offset | Applied at extract (`-O`) when calibrated |
| Test and copy | Two secure passes; CRC32 must match (retries on mismatch) |
| AccurateRip | Verifies offset-corrected audio against the public AR DB |
| CTDB | Complements AccurateRip with CUETools track CRCs |
| Burst fallback | Off by default; optional insecure `-Z` after secure retries fail |

### Settings cheat sheet

| Group | Options (defaults) |
|-------|--------------------|
| Paths | Output folder (`~/Music`); album/track templates |
| Encoder | FLAC (level 5), MP3, AAC, Opus, WAV |
| Extraction | Test & copy **on**; Copy Image **off**; HTOA **on**; data tracks **on** |
| Verify | AccurateRip **on**; CTDB **on**; CTDB submit **off** |
| Output | Write log **on**; write `.cue` **on**; auto-rip / auto-eject **off** |

## Develop from source

| Distro | Packages |
|--------|----------|
| Fedora | `python3-gobject gtk4 libadwaita python3-mutagen python3-pillow cdparanoia flac lame ffmpeg opus-tools` |
| Debian / Ubuntu | `python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 python3-mutagen python3-pil cdparanoia flac lame ffmpeg opus-tools` |

```bash
./run                          # or: PYTHONPATH=src python3 -m ready2rip.main
meson setup build && meson compile -C build
```

### Package locally

| Target | Command | Output |
|--------|---------|--------|
| AppImage | `./appimage/build-appimage.sh` | `dist/ready2rip-VERSION-<arch>.AppImage` |
| deb + rpm | `./packaging/build-deb-rpm.sh` | `dist/*.deb`, `dist/*.rpm` |
| Flatpak | `./packaging/build-flatpak.sh` | `dist/ready2rip-VERSION-<arch>.flatpak` |

AppImage signing (optional local): set `SIGN=1`, `SIGN_KEY=<fpr>`, and `APPIMAGETOOL_SIGN_PASSPHRASE`.
CI signs release AppImages with the project key automatically.

Build AppImages on the oldest glibc you care about for wider reach — glibc is not bundled.

## Inspired by

[Exact Audio Copy](https://www.exactaudiocopy.de/) · [dBpoweramp](https://www.dbpoweramp.com/) · [cyanrip](https://github.com/cyanreg/cyanrip)

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).
