"""Entry script for the frozen application."""
import sys

from fiberlume_boot import activate

def verify_install(folder):
    import pandas as pd
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication

    from fiberphoto import PipelineConfig, __version__, run_file
    from fiberphoto.gui.main_window import MainWindow, manual_path
    from fiberphoto.sample_data import make_sample_dataset

    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(folder))
    app = QApplication(sys.argv[:1])
    window = MainWindow(lang="en")
    window.show()
    app.processEvents()
    for lang in ("en", "zh"):
        if manual_path(lang) is None:
            raise RuntimeError(f"Missing {lang} manual")
    paths = make_sample_dataset(folder / "data", n_animals=1, duration_s=180)
    if window.windowIcon().isNull() or "FiberLume" not in window.windowTitle():
        raise RuntimeError("Missing FiberLume branding")
    window.add_paths(paths)
    cfg = PipelineConfig()
    cfg.plot.formats = ["png"]
    cfg.plot.dpi = 72
    result = run_file(paths[0], cfg, folder / "results")
    if result.error or result.C is None or not len(result.peth.trials) or not result.figures:
        raise RuntimeError(result.error or "Incomplete analysis")
    export = folder / "sample_PhotoData.csv"
    ratio = result.A.signal / result.A.signal.mean() - 1
    pd.DataFrame({"time": result.A.time, "RawF_1": result.A.signal, "R_1": ratio}).to_csv(export, index=False)
    window.add_paths([export])
    window.file_list.setCurrentRow(1)
    if window.cb_signal.currentText() != "RawF_1":
        raise RuntimeError("Plexon channel mapping failed")
    imported = run_file(export, cfg, folder / "plexon-results")
    if imported.error or imported.C is None or imported.B.method != "Plexon R_1":
        raise RuntimeError(imported.error or "Incomplete Plexon analysis")
    if not window.a_update.isEnabled():
        raise RuntimeError("Update action unavailable")
    (folder / "running-version.txt").write_text(__version__, encoding="ascii")
    window.close()


if __name__ == "__main__":
    from pathlib import Path
    qa_folder = Path(sys.argv[2]) if sys.argv[1:2] == ["--verify-install"] else None
    active_patch = activate(qa_folder / "patches" if qa_folder is not None else None)
    if sys.argv[1:2] == ["--verify-install"]:
        import traceback
        from pathlib import Path

        folder = Path(sys.argv[2])
        folder.mkdir(parents=True, exist_ok=True)
        try:
            verify_install(folder)
        except Exception:
            (folder / "verify-error.txt").write_text(traceback.format_exc(), encoding="utf-8")
            sys.exit(1)
    else:
        try:
            from fiberphoto.gui.app import main
            sys.exit(main())
        except Exception:
            if active_patch:
                (active_patch / ".disabled").touch()
            raise
