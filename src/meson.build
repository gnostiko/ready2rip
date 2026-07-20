configure_file(
  input: 'ready2rip.in',
  output: 'ready2rip',
  configuration: conf,
  install: true,
  install_dir: bindir,
  install_mode: 'rwxr-xr-x',
)

# Install the Python package under pkgdatadir so the launcher can import it.
# Exclude templates and the dev fallback config — the generated config.py is
# installed separately below.
install_subdir(
  'ready2rip',
  install_dir: pkgdatadir,
  exclude_files: ['config.py', 'config.py.in'],
  exclude_directories: ['__pycache__'],
  # native/ar_offset_scan.c is shipped as source and compiled into the user cache
)

# Generated runtime config (paths, version, app id).
configure_file(
  input: 'ready2rip/config.py.in',
  output: 'config.py',
  configuration: conf,
  install: true,
  install_dir: pkgdatadir / 'ready2rip',
)
