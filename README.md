# ready2rip

Modern GNOME CD ripping application for Linux (Flatpak-friendly).

**Status:** initial release candidate — GTK 4 / libadwaita UI with cdparanoia
secure rip, AccurateRip, MusicBrainz, tags, ReplayGain, and artwork.

Default rip destination is the **XDG Music** folder (`~/Music`).

**Repository:** [github.com/gnostiko/ready2rip](https://github.com/gnostiko/ready2rip)  
**Author:** [gnostiko](https://github.com/gnostiko)

## Identity

| Item | Value |
|------|--------|
| Display name | ready2rip |
| Application ID | `org.ready2rip.Ready2Rip` |
| Python package | `ready2rip` |
| Binary | `ready2rip` |
| GSettings schema | `org.ready2rip.Ready2Rip` |
| License | GPL-3.0-or-later |

## Goals

- Secure ripping with **cdparanoia**
- **AccurateRip** verification
- Metadata from **MusicBrainz** and FreeDB-compatible services (e.g. gnudb)
- Tags including **ReplayGain**
- Album art (iTunes, Cover Art Archive, Deezer), with optional embed sizes
- **GTK 4 + libadwaita** UI, distributed via **Flatpak**

## Project layout

```text
ready2rip/                  # project folder (may still be named GCDRIP on disk)
  data/                     # desktop, icons, AppStream, GSettings
  flatpak/                  # Flatpak manifest
  po/                       # translations
  src/ready2rip/
    application.py
    window.py
    disc/
    rip/
    metadata/
    tags/
    artwork/
    accuraterip.py
  meson.build
```

## Develop on the host (Solus / any GNOME)

### Dependencies

- Python 3 + PyGObject (Gtk 4, Adwaita 1)
- Meson, Ninja
- `cdparanoia` (for disc detection and ripping)
- Optional: `flatpak-builder`, GNOME SDK 50

### Run without installing

```bash
cd "/path/to/ready2rip"   # or the GCDRIP folder if not renamed on disk
PYTHONPATH=src GSETTINGS_SCHEMA_DIR=build/data python3 -m ready2rip.main
```

### Build with Meson

```bash
rm -rf build
meson setup build
meson compile -C build
# Optional local install:
meson setup build --prefix="$PWD/install" --reconfigure
meson install -C build
./install/bin/ready2rip
```

## Flatpak

```bash
flatpak install flathub org.gnome.Platform//50 org.gnome.Sdk//50
flatpak-builder --user --install --force-clean build-flatpak flatpak/org.ready2rip.Ready2Rip.yml
flatpak run org.ready2rip.Ready2Rip
```

The manifest requests `--device=all` so optical drives are visible in the sandbox.

## License

GPL-3.0-or-later (intended).
