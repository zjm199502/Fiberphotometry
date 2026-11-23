"""Exercise the installed Cocoa GUI via the normal event loop in a disposable CI home."""
import os
from pathlib import Path
import sys
import time
import traceback


def exercise(window):
    from PySide6.QtCore import QThread
    from PySide6.QtWidgets import QApplication
    from fiberphoto import __version__
    from fiberphoto.sample_data import make_sample_dataset
    import pandas as pd
    import numpy as np

    folder = Path(os.environ["READ_QA"])
    app = QApplication.instance()
    failures = []
    window.error = failures.append
    try:
        assert app.platformName() == "cocoa"
        assert QThread.currentThread() == app.thread()
        paths = make_sample_dataset(folder, n_animals=1, duration_s=180)
        csv = folder / "Plexon.csv"
        t = np.arange(145000) / 30.
        pd.DataFrame({"time": t, "RawF_1": 100 + np.sin(t), "R_1": np.sin(t)/100}).to_csv(csv, index=False)
        window.add_paths([*paths, csv])
        for i in (0, 1, 0, 1):
            window.file_list.setCurrentRow(i)
            print("Reading", window.current.name, flush=True)
            window.b_read.click()
            deadline = time.monotonic() + 60
            while window._busy and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(.01)
            assert not window._busy and not failures, failures
            assert window.A is not None
            app.processEvents()
            window.fig_raw.canvas.draw()
            assert not window.grab().isNull()
        (folder/"gui-ok").write_text(__version__)
        window.close()
    except BaseException:
        traceback.print_exc()
        sys.stderr.flush()
        os._exit(1)


if __name__ == "__main__":
    import shutil
    # The probe only instruments a sandbox copy of the production patch.
    package = Path(sys.argv[1]) / "fiberphoto/gui"
    shutil.copyfile(__file__, package/"_mac_read_probe.py")
    target = package/"app.py"
    content = target.read_text()
    anchor = "    return app.exec()"
    assert content.count(anchor) == 1
    target.write_text(content.replace(anchor,
        "    from ._mac_read_probe import exercise\n"
        "    from PySide6.QtCore import QTimer\n"
        "    QTimer.singleShot(100, lambda: exercise(win))\n" + anchor))
