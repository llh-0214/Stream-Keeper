from __future__ import annotations

import multiprocessing
import sys
from pathlib import Path

from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from .recorder import RecorderManager
from .storage import Storage
from .ui import MainWindow, apply_style


def main() -> int:
    multiprocessing.freeze_support()
    app = QApplication(sys.argv)
    app.setApplicationName("直播守候")
    app.setApplicationDisplayName("直播守候")
    app.setOrganizationName("StreamKeeper")
    app.setQuitOnLastWindowClosed(False)
    bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    icon_path = bundle_root / "assets" / "app-icon-concept-v2.png"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    apply_style(app)
    storage = Storage()
    manager = RecorderManager(storage)
    window = MainWindow(storage, manager)
    tray = QSystemTrayIcon(app.windowIcon(), app)
    tray.setToolTip("直播守候 · 后台监测中")
    tray_menu = QMenu(window)
    show_action = QAction("打开直播守候", tray_menu)
    quit_action = QAction("退出直播守候", tray_menu)
    tray_menu.addAction(show_action)
    tray_menu.addSeparator()
    tray_menu.addAction(quit_action)
    tray.setContextMenu(tray_menu)
    show_action.triggered.connect(window.show_from_tray)
    quit_action.triggered.connect(window.request_quit)
    tray.activated.connect(
        lambda reason: window.show_from_tray()
        if reason in {QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick}
        else None
    )
    window.set_tray_icon(tray)
    app.aboutToQuit.connect(manager.shutdown)
    tray.show()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
