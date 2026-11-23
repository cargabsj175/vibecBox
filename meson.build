project(
  'vibecBox',
  version: '0.1.0',
  meson_version: '>=1.4.0',
  license: 'MIT',
)

py = import('python').find_installation('python3', required: true)

prefix_abs = get_option('prefix')
datadir_abs = prefix_abs / get_option('datadir')
appdir_abs = datadir_abs / 'vibecBox'
bindir_abs = prefix_abs / get_option('bindir')

conf = configuration_data()
conf.set('PYTHON', py.full_path())
conf.set('APPDIR', appdir_abs)
conf.set('BINDIR', bindir_abs)

install_data(
  files(
    'vibecBox_core.py',
    'vibecBox_gui.py',
    'models.py',
    'qemu.py',
    'runner.py',
    'requirements.txt',
  ),
  install_dir: get_option('datadir') / 'vibecBox',
)

install_data(
  files('LICENSE', 'README.md'),
  install_dir: get_option('datadir') / 'doc' / 'vibecBox',
)

configure_file(
  input: 'vibecbox.in',
  output: 'vibecbox',
  configuration: conf,
  install: true,
  install_dir: get_option('bindir'),
  install_mode: 'rwxr-xr-x',
)

desktop = configure_file(
  input: 'vibecbox.desktop.in',
  output: 'vibecbox.desktop',
  configuration: conf,
  install: true,
  install_dir: get_option('datadir') / 'applications',
)

foreach size : ['16', '32', '48', '64', '128', '256']
  install_data(
    'icons/' + size + 'x' + size + '/apps/vibecbox.png',
    install_dir: get_option('datadir') / 'icons' / 'hicolor' /
      (size + 'x' + size) / 'apps',
  )
endforeach

validate_desktop = find_program('desktop-file-validate', required: false)
if validate_desktop.found()
  test('validate-desktop', validate_desktop, args: [desktop.full_path()])
endif

meson.add_install_script(
  'scripts/post_install.py',
  get_option('prefix'),
  get_option('datadir'),
)
