from __future__ import annotations

import multiprocessing
import sys

from PySide6.QtWidgets import QApplication

from .recorder import RecorderManager
from .storage import Storage
from .ui import MainWindow, apply_style


def main() -> int:
    multiprocessing.freeze_support()
    app = QApplication(sys.argv)
    app.setApplicationName("直播守候")
    app.setApplicationDisplayName("直播守候")
    app.setOrganizationName("StreamKeeper")
    app.setQuitOnLastWindowClosed(True)
    apply_style(app)
    storage = Storage()
    manager = RecorderManager(storage)
    window = MainWindow(storage, manager)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

