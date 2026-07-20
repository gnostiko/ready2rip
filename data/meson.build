desktop_file = configure_file(
  input: 'org.ready2rip.Ready2Rip.desktop.in',
  output: 'org.ready2rip.Ready2Rip.desktop',
  configuration: conf,
  install: true,
  install_dir: datadir / 'applications',
)

appstream_file = configure_file(
  input: 'org.ready2rip.Ready2Rip.metainfo.xml.in',
  output: 'org.ready2rip.Ready2Rip.metainfo.xml',
  configuration: conf,
  install: true,
  install_dir: datadir / 'metainfo',
)

install_data(
  'icons/hicolor/scalable/apps/org.ready2rip.Ready2Rip.svg',
  install_dir: datadir / 'icons/hicolor/scalable/apps',
)

install_data(
  'icons/hicolor/symbolic/apps/org.ready2rip.Ready2Rip-symbolic.svg',
  install_dir: datadir / 'icons/hicolor/symbolic/apps',
)

# GSettings schema
install_data(
  'org.ready2rip.Ready2Rip.gschema.xml',
  install_dir: datadir / 'glib-2.0/schemas',
)

compile_schemas = find_program('glib-compile-schemas', required: false)
if compile_schemas.found()
  test(
    'validate-gschema',
    compile_schemas,
    args: ['--strict', '--dry-run', meson.current_source_dir()],
  )
endif
