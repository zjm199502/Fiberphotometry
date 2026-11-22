"""Provide this build's plotting sources to independent plot-data ZIPs."""
from contextlib import nullcontext
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

PACKAGE = Path(__file__).resolve().parent
FILES = ("replot_script", "plotting", "display_traces", "i18n", "figure_style", "analysis",
         "config", "data", "behavior", "abet", "abet_resampling")


def snapshot_path(package):
    return package / "gui/assets/renderer-source.zip"


def build_snapshot(package=PACKAGE):
    """A fresh standalone build collects gui/assets through its existing PyInstaller spec."""
    with ZipFile(snapshot_path(package), "w", ZIP_DEFLATED) as output:
        for name in FILES:
            output.write(package / (name + ".py"), name + ".py")


def add_renderer(archive, package=PACKAGE):
    # A cumulative patch ships ordinary .py files. Frozen base builds have an asset snapshot.
    from_disk = (package / "replot_script.py").is_file()
    archive.writestr("fiberlume_render/__init__.py", "")
    with (nullcontext() if from_disk else ZipFile(snapshot_path(package))) as saved:
        for name in FILES:
            dest = "replot.py" if name == "replot_script" else "fiberlume_render/" + name + ".py"
            data = saved.read(name + ".py") if saved else (package / (name + ".py")).read_bytes()
            archive.writestr(dest, data)


if __name__ == "__main__":
    build_snapshot()
