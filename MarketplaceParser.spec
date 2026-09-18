# PyInstaller build: python -m PyInstaller MarketplaceParser.spec --noconfirm
#
# The result is dist/MarketplaceParser/MarketplaceParser.exe (a folder build starts much faster than
# a single file, because the bundled Patchright driver does not have to be unpacked on every launch).
# The application drives the Chrome or Edge already installed on the machine, so no browser is bundled.

from PyInstaller.utils.hooks import collect_data_files

datas = [("assets/icon.ico", "assets")]
datas += collect_data_files("patchright")  # node.exe + driver package
datas += collect_data_files("customtkinter")  # themes and fonts

analysis = Analysis(
    ["app.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=["patchright", "openpyxl"],
    excludes=["matplotlib", "numpy", "pandas", "pytest", "tkinter.test", "test"],
    noarchive=False,
)
pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="MarketplaceParser",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    icon="assets/icon.ico",
    version_info=None,
)

collection = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="MarketplaceParser",
)
