# PyInstaller build: python -m PyInstaller MarketplaceParser.spec --noconfirm
#
# The result is dist/MarketplaceParser/MarketplaceParser.exe (a folder build starts much faster than
# a single file, because the bundled Patchright driver does not have to be unpacked on every launch).
# The application drives the Chrome or Edge already installed on the machine, so no browser is bundled.
#
# Marketplace plugins installed in the environment are bundled too: their code and — importantly — their
# package metadata, which is how the program finds them. Nothing has to be listed here by name.

from importlib.metadata import distributions

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

ENTRY_POINT_GROUP = "mpparser.marketplaces"

datas = [("assets/icon.ico", "assets")]
datas += collect_data_files("patchright")  # node.exe + driver package
datas += collect_data_files("customtkinter")  # themes and fonts

hiddenimports = ["patchright", "openpyxl"]
for distribution in distributions():
    entry_points = [ep for ep in distribution.entry_points if ep.group == ENTRY_POINT_GROUP]
    name = distribution.metadata["Name"]
    if not entry_points or name == "mpparser":
        continue
    print(f"[spec] marketplace plugin: {name}")
    datas += copy_metadata(name)  # without the metadata the entry point is invisible
    for entry_point in entry_points:
        hiddenimports += collect_submodules(entry_point.value.split(":")[0].split(".")[0])

analysis = Analysis(
    ["app.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
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
