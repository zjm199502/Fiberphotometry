# PyInstaller spec: builds a double-clickable desktop app.
# The sample dataset is generated on first use ("Sample data" button), so it is not bundled.
#   pip install -e .[gui] pyinstaller
#   pyinstaller packaging/fiberphoto.spec --noconfirm
# Output: dist/fiberphoto/ (Windows/Linux folder) or dist/fiberphoto.app (macOS)
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

root = Path(SPECPATH).parent

a = Analysis(
    [str(root / "packaging" / "launch.py")],
    pathex=[str(root / "src")],
    datas=[(str(root / "src" / "fiberphoto" / "gui" / "manual"), "fiberphoto/gui/manual")],
    hiddenimports=collect_submodules("oasis") + collect_submodules("nptdms") + ["openpyxl"],
    excludes=["PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore",
              "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtPdf", "tkinter", "IPython"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="fiberphoto",
          console=False, icon=None)
coll = COLLECT(exe, a.binaries, a.datas, name="fiberphoto")
if sys.platform == "darwin":
    app = BUNDLE(coll, name="fiberphoto.app", bundle_identifier="org.fiberphoto.app",
                 info_plist={"NSHighResolutionCapable": True})
