project(
  'ready2rip',
  version: '0.4.1',
  meson_version: '>= 1.0.0',
  default_options: ['warning_level=2', 'prefix=/usr'],
)

python = import('python')
gnome = import('gnome')

py_installation = python.find_installation('python3')

# Application identity
APPLICATION_ID = 'org.ready2rip.Ready2Rip'
APPLICATION_NAME = 'ready2rip'
APPLICATION_VERSION = meson.project_version()
GETTEXT_PACKAGE = meson.project_name()
PACKAGE_URL = 'https://github.com/gnostiko/ready2rip'
BUGTRACKER_URL = 'https://github.com/gnostiko/ready2rip/issues'

prefix = get_option('prefix')
bindir = prefix / get_option('bindir')
datadir = prefix / get_option('datadir')
pkgdatadir = datadir / meson.project_name()
localedir = prefix / get_option('localedir')

conf = configuration_data()
conf.set('APPLICATION_ID', APPLICATION_ID)
conf.set('APPLICATION_NAME', APPLICATION_NAME)
conf.set('APPLICATION_VERSION', APPLICATION_VERSION)
conf.set('GETTEXT_PACKAGE', GETTEXT_PACKAGE)
conf.set('LOCALEDIR', localedir)
conf.set('PKGDATADIR', pkgdatadir)
conf.set('PYTHON', py_installation.full_path())
conf.set('VERSION', APPLICATION_VERSION)
conf.set('PACKAGE_URL', PACKAGE_URL)
conf.set('BUGTRACKER_URL', BUGTRACKER_URL)

subdir('data')
subdir('src')

# Uninstalled run: meson compile -C build run
# (or ./run from the source tree — no Meson required)
run_env = environment()
run_env.prepend('PYTHONPATH', meson.project_source_root() / 'src')
run_env.set('GSETTINGS_SCHEMA_DIR', meson.project_build_root() / 'data')
meson.add_devenv(run_env)

run_target(
  'run',
  command: [py_installation, '-m', 'ready2rip.main'],
  env: run_env,
)

gnome.post_install(
  glib_compile_schemas: true,
  gtk_update_icon_cache: true,
  update_desktop_database: true,
)
