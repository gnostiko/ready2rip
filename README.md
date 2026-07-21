# ready2rip

A vibe-coded personal project.

I love dBpoweramp and EAC, but was unsatisfied with CD rippers available on Linux. I needed something that would automatically ensure a secure ripping process similar to EAC, pull the highest quality art, resize embedded art, and process ReplayGain v2.0. This accompishes all of that in a GNOME style GUI.

**Status:** initial release candidate — GTK 4 / libadwaita UI with cdparanoia
secure rip, AccurateRip, MusicBrainz, tags, ReplayGain, and artwork.


**Repository:** [github.com/gnostiko/ready2rip](https://github.com/gnostiko/ready2rip)  
**Author:** [gnostiko](https://github.com/gnostiko)


## Features

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

## Dependencies

- Python 3 + PyGObject (Gtk 4, Adwaita 1)
- Meson, Ninja
- `cdparanoia` (for disc detection and ripping)
- Optional: `flatpak-builder`, GNOME SDK 50



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



## License

GPL-3.0-or-later (intended).
