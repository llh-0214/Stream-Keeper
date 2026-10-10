from __future__ import annotations

import multiprocessing
import sys
from pathlib import Path

from PySide6.QtGui import QIcon
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
    bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    icon_path = bundle_root / "assets" / "app-icon-concept-v2.png"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    apply_style(app)
    storage = Storage()
    manager = RecorderManager(storage)
    window = MainWindow(storage, manager)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
