# PyInstaller onedir build for Windows. Build with:
#   python -m PyInstaller --noconfirm --clean tutoring_organiser.spec
from PyInstaller.utils.hooks import collect_all, collect_submodules

datas = [("assets/icon.ico", "assets")]   # window/taskbar icon at run time (see config.ICON_PATH)
binaries = []
hiddenimports = []
for package in ("googleapiclient", "google_auth_oauthlib", "google.auth"):
    package_datas, package_binaries, package_hidden = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden

# Every module of the app package, even ones only imported lazily.
hiddenimports += collect_submodules("tutoring_organiser")

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=["rth_pyside6_dlls.py"],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="TutoringOrganiser",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/icon.ico",
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="TutoringOrganiser",
)
