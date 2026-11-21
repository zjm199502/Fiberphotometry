"""Entry script for the frozen application."""
import sys

from fiberphoto.gui.app import main

if __name__ == "__main__":
    if sys.argv[1:2] == ["--verify-install"]:
        from pathlib import Path

        from PySide6.QtCore import QSettings
        from PySide6.QtWidgets import QApplication

        from fiberphoto import PipelineConfig, run_file
        from fiberphoto.gui.main_window import MainWindow, manual_path
        from fiberphoto.sample_data import make_sample_dataset

        folder = Path(sys.argv[2])
        folder.mkdir(parents=True, exist_ok=True)
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
        window.add_paths(paths)
        cfg = PipelineConfig()
        cfg.plot.formats = ["png"]
        cfg.plot.dpi = 72
        result = run_file(paths[0], cfg, folder / "results")
        if result.error or result.C is None or not len(result.peth.trials) or not result.figures:
            raise RuntimeError(result.error or "Incomplete analysis")
        window.close()
        sys.exit(0)
    sys.exit(main())
