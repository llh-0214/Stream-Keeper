from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QSystemTrayIcon,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .models import Platform, RecordingOptions, Room, RoomStatus
from .recorder import RecorderManager
from .storage import Storage
from .urls import UnsupportedPlatform, normalize_and_detect

STATUS_COLORS = {
    RoomStatus.PAUSED.value: "#7A8499",
    RoomStatus.CHECKING.value: "#E3A738",
    RoomStatus.OFFLINE.value: "#65A98B",
    RoomStatus.LIVE.value: "#35B57B",
    RoomStatus.RECORDING.value: "#FF5A63",
    RoomStatus.PROTECTED.value: "#B66A12",
    RoomStatus.ERROR.value: "#E25555",
}


class AddRoomDialog(QDialog):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("添加直播间")
        self.setMinimumWidth(480)
        layout = QVBoxLayout(self)
        title = QLabel("添加一个直播间")
        title.setObjectName("dialogTitle")
        help_text = QLabel("粘贴抖音、小红书、哔哩哔哩直播间链接，或可直接播放的直播流地址。")
        help_text.setWordWrap(True)
        help_text.setObjectName("muted")
        layout.addWidget(title)
        layout.addWidget(help_text)
        form = QFormLayout()
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("直播间链接，或 https://...m3u8 / rtmp://... 直播流")
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("可选，例如：小明的直播")
        self.quality = QComboBox()
        self.quality.addItem("原画", "OD")
        self.quality.addItem("超清", "UHD")
        self.quality.addItem("高清", "HD")
        self.quality.addItem("标清", "SD")
        self.quality.addItem("流畅", "LD")
        form.addRow("直播间链接", self.url_edit)
        form.addRow("备注名称", self.name_edit)
        form.addRow("录制画质", self.quality)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Ok)
        buttons.button(QDialogButtonBox.Ok).setText("添加并监测")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def result_data(self) -> tuple[str, Platform, str, str]:
        url, platform = normalize_and_detect(self.url_edit.text())
        return url, platform, self.name_edit.text().strip(), str(self.quality.currentData())


class EditRoomDialog(QDialog):
    def __init__(self, room: Room, parent: QWidget | None = None):
        super().__init__(parent)
        self.room = room
        self.delete_requested = False
        self.setWindowTitle("编辑直播间")
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name_edit = QLineEdit(room.display_name)
        self.quality = QComboBox()
        qualities = [("原画", "OD"), ("超清", "UHD"), ("高清", "HD"), ("标清", "SD"), ("流畅", "LD")]
        for label, value in qualities:
            self.quality.addItem(label, value)
        self.quality.setCurrentIndex(max(0, self.quality.findData(room.quality)))
        self.enabled = QCheckBox("自动监测开播状态")
        self.enabled.setChecked(room.enabled)
        form.addRow("备注名称", self.name_edit)
        form.addRow("录制画质", self.quality)
        form.addRow("", self.enabled)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Save)
        buttons.button(QDialogButtonBox.Save).setText("保存")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        delete = buttons.addButton("删除直播间", QDialogButtonBox.DestructiveRole)
        delete.setObjectName("dangerText")
        delete.clicked.connect(self._request_delete)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _request_delete(self) -> None:
        self.delete_requested = True
        self.accept()


class MainWindow(QMainWindow):
    def __init__(self, storage: Storage, manager: RecorderManager):
        super().__init__()
        self.storage = storage
        self.manager = manager
        self.rooms: dict[int, Room] = {}
        self.room_rows: dict[int, int] = {}
        self.recording_started: dict[int, float] = {}
        self.tray_icon: QSystemTrayIcon | None = None
        self._tray_notice_shown = False
        self._quitting = False
        self.setWindowTitle("直播守候 · 多平台直播录制")
        self.resize(1120, 760)
        self.setMinimumSize(920, 640)
        self._build_ui()
        self._connect_signals()
        self.load_rooms()
        self.load_settings()
        self.load_history()
        self.manager.start()

        self.elapsed_timer = QTimer(self)
        self.elapsed_timer.timeout.connect(self._refresh_elapsed)
        self.elapsed_timer.start(1000)

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        main = QHBoxLayout(root)
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(194)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(22, 28, 18, 22)
        brand_row = QHBoxLayout()
        brand_icon = QLabel()
        brand_icon.setPixmap(QApplication.instance().windowIcon().pixmap(38, 38))
        brand = QLabel("直播守候")
        brand.setObjectName("brand")
        brand_row.addWidget(brand_icon)
        brand_row.addSpacing(6)
        brand_row.addWidget(brand)
        brand_row.addStretch()
        side.addLayout(brand_row)
        side.addSpacing(50)
        self.nav_buttons: list[QPushButton] = []
        for label, index in [("☷   录制任务", 0), ("◷   历史记录", 1), ("⚙   设置", 2)]:
            button = QPushButton(label)
            button.setObjectName("navButton")
            button.setCheckable(True)
            button.clicked.connect(lambda checked=False, i=index: self._navigate(i))
            side.addWidget(button)
            self.nav_buttons.append(button)
        self.nav_buttons[0].setChecked(True)
        side.addStretch()
        privacy = QLabel("数据仅保存在本机")
        privacy.setObjectName("privacy")
        privacy.setAlignment(Qt.AlignCenter)
        side.addWidget(privacy)
        main.addWidget(sidebar)

        self.pages = QStackedWidget()
        self.pages.addWidget(self._dashboard_page())
        self.pages.addWidget(self._history_page())
        self.pages.addWidget(self._settings_page())
        main.addWidget(self.pages, 1)

    def _page_header(self, title: str, subtitle: str) -> tuple[QVBoxLayout, QHBoxLayout]:
        layout = QVBoxLayout()
        layout.setContentsMargins(32, 28, 32, 28)
        header = QHBoxLayout()
        text = QVBoxLayout()
        heading = QLabel(title)
        heading.setObjectName("pageTitle")
        detail = QLabel(subtitle)
        detail.setObjectName("muted")
        text.addWidget(heading)
        text.addWidget(detail)
        header.addLayout(text)
        header.addStretch()
        layout.addLayout(header)
        return layout, header

    def _dashboard_page(self) -> QWidget:
        page = QWidget()
        layout, header = self._page_header("录制任务", "正在监测 0 个直播间")
        self.monitoring_label = header.itemAt(0).layout().itemAt(1).widget()
        add = QPushButton("＋ 添加直播间")
        add.setObjectName("primaryButton")
        add.clicked.connect(self.add_room)
        header.addWidget(add)

        layout.addSpacing(28)
        panel = QFrame()
        panel.setObjectName("taskPanel")
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(0)
        panel_title = QLabel("直播间")
        panel_title.setObjectName("panelTitle")
        panel_title.setContentsMargins(24, 20, 24, 18)
        panel_layout.addWidget(panel_title)

        self.room_table = QTableWidget(0, 4)
        self.room_table.setHorizontalHeaderLabels(["直播间", "平台", "状态", "操作"])
        self.room_table.horizontalHeader().setVisible(False)
        self.room_table.verticalHeader().setVisible(False)
        self.room_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.room_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.room_table.setShowGrid(False)
        self.room_table.setAlternatingRowColors(False)
        self.room_table.setMinimumHeight(160)
        self.room_table.cellDoubleClicked.connect(lambda row, _column: self.edit_room_by_row(row))
        header_view = self.room_table.horizontalHeader()
        header_view.setSectionResizeMode(0, QHeaderView.Stretch)
        header_view.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header_view.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header_view.setSectionResizeMode(3, QHeaderView.Fixed)
        header_view.resizeSection(3, 120)
        panel_layout.addWidget(self.room_table)
        hint = QLabel("关闭窗口后，监测仍会在后台继续")
        hint.setObjectName("panelHint")
        hint.setContentsMargins(24, 16, 24, 16)
        panel_layout.addWidget(hint)
        layout.addWidget(panel)
        layout.addStretch()
        page.setLayout(layout)
        return page

    def _history_page(self) -> QWidget:
        page = QWidget()
        layout, header = self._page_header("历史记录", "查看已完成、停止或失败的录制")
        refresh = QPushButton("刷新")
        refresh.clicked.connect(self.load_history)
        header.addWidget(refresh)
        self.history_table = QTableWidget(0, 7)
        self.history_table.setHorizontalHeaderLabels(["平台", "主播", "标题", "开始时间", "结束时间", "结果", "位置"])
        self.history_table.verticalHeader().setVisible(False)
        self.history_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.history_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.history_table.setShowGrid(False)
        self.history_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.history_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        layout.addSpacing(20)
        layout.addWidget(self.history_table, 1)
        page.setLayout(layout)
        return page

    def _settings_page(self) -> QWidget:
        page = QWidget()
        layout, _ = self._page_header("偏好设置", "录制参数仅保存在本机，不会随安装包分发")
        panel = QFrame()
        panel.setObjectName("settingsPanel")
        form = QFormLayout(panel)
        form.setSpacing(16)
        output_row = QHBoxLayout()
        self.output_edit = QLineEdit()
        choose = QPushButton("选择目录")
        choose.clicked.connect(self.choose_output)
        output_row.addWidget(self.output_edit, 1)
        output_row.addWidget(choose)
        self.segment_spin = QSpinBox()
        self.segment_spin.setRange(1, 360)
        self.segment_spin.setSuffix(" 分钟")
        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(15, 3600)
        self.interval_spin.setSuffix(" 秒")
        self.dy_cookie = QLineEdit()
        self.dy_cookie.setEchoMode(QLineEdit.Password)
        self.dy_cookie.setPlaceholderText("可选；遇到风控或无法解析时填写")
        self.xhs_cookie = QLineEdit()
        self.xhs_cookie.setEchoMode(QLineEdit.Password)
        self.xhs_cookie.setPlaceholderText("可选；遇到登录限制时填写")
        form.addRow("视频保存目录", output_row)
        form.addRow("每段视频长度", self.segment_spin)
        form.addRow("未开播检查间隔", self.interval_spin)
        form.addRow("抖音 Cookie", self.dy_cookie)
        form.addRow("小红书 Cookie", self.xhs_cookie)
        warning = QLabel("Cookie 等同于登录凭证，请勿截图分享或上传到代码仓库。")
        warning.setObjectName("warning")
        warning.setWordWrap(True)
        form.addRow("", warning)
        sleep_note = QLabel("监测期间会阻止电脑自动休眠，但允许屏幕正常熄灭。合上笔记本盖仍会休眠。")
        sleep_note.setObjectName("muted")
        sleep_note.setWordWrap(True)
        form.addRow("后台运行", sleep_note)
        save = QPushButton("保存设置")
        save.setObjectName("primaryButton")
        save.clicked.connect(self.save_settings)
        form.addRow("", save)
        layout.addSpacing(20)
        layout.addWidget(panel)
        layout.addStretch()
        page.setLayout(layout)
        return page

    def _connect_signals(self) -> None:
        self.manager.room_status.connect(self.update_room_status)
        self.manager.recording_started.connect(self.on_recording_started)
        self.manager.recording_finished.connect(self.on_recording_finished)
        self.manager.counts_changed.connect(self.update_counts)
        self.manager.history_changed.connect(self.load_history)

    def _navigate(self, index: int) -> None:
        self.pages.setCurrentIndex(index)
        for i, button in enumerate(self.nav_buttons):
            button.setChecked(i == index)
        if index == 1:
            self.load_history()

    def load_rooms(self) -> None:
        room_list = self.storage.list_rooms()
        self.rooms = {room.id: room for room in room_list}
        self.room_rows.clear()
        self.room_table.setRowCount(len(room_list))
        for row, room in enumerate(room_list):
            self.room_rows[room.id] = row
            self._render_room(row, room)
        self.update_counts(sum(room.enabled for room in room_list), len(self.recording_started))

    def _render_room(self, row: int, room: Room) -> None:
        label = room.display_name or room.platform.label + "直播间"
        name_item = QTableWidgetItem(label)
        name_item.setData(Qt.UserRole, room.id)
        name_item.setToolTip(room.url)
        self.room_table.setItem(row, 0, name_item)
        platform_item = QTableWidgetItem(room.platform.label)
        platform_item.setForeground(QColor("#7A8499"))
        self.room_table.setItem(row, 1, platform_item)
        self._set_status_cell(row, room)
        self._set_action_cell(row, room)
        self.room_table.setRowHeight(row, 72)

    def _set_status_cell(self, row: int, room: Room, elapsed: str = "") -> None:
        labels = {
            RoomStatus.OFFLINE: "等待开播",
            RoomStatus.CHECKING: "正在检测",
            RoomStatus.LIVE: "准备录制",
        }
        label = labels.get(room.status, room.status.label)
        if room.status is RoomStatus.RECORDING and elapsed:
            label = f"录制中 · {elapsed}"
        item = QTableWidgetItem(f"●  {label}")
        item.setForeground(QColor(STATUS_COLORS[room.status.value]))
        item.setToolTip(room.last_message)
        self.room_table.setItem(row, 2, item)

    def _set_action_cell(self, row: int, room: Room) -> None:
        actions = QWidget()
        action_layout = QHBoxLayout(actions)
        action_layout.setContentsMargins(4, 8, 14, 8)
        if room.status is RoomStatus.RECORDING:
            button = QPushButton("停止")
            button.setObjectName("dangerText")
            button.clicked.connect(lambda checked=False, rid=room.id: self.manager.stop_recording(rid))
        else:
            button = QPushButton("编辑")
            button.clicked.connect(lambda checked=False, rid=room.id: self.edit_room(rid))
        button.setFixedWidth(82)
        action_layout.addStretch()
        action_layout.addWidget(button)
        self.room_table.setCellWidget(row, 3, actions)

    def edit_room_by_row(self, row: int) -> None:
        item = self.room_table.item(row, 0)
        if item is not None:
            self.edit_room(int(item.data(Qt.UserRole)))

    def edit_room(self, room_id: int) -> None:
        room = self.rooms[room_id]
        dialog = EditRoomDialog(room, self)
        if dialog.exec() != QDialog.Accepted:
            return
        if dialog.delete_requested:
            self.remove_room(room_id)
            return
        room.display_name = dialog.name_edit.text().strip()
        room.quality = str(dialog.quality.currentData())
        room.enabled = dialog.enabled.isChecked()
        room.status = RoomStatus.OFFLINE if room.enabled else RoomStatus.PAUSED
        self.storage.update_room(room)
        self.load_rooms()
        self.manager.wake()

    def add_room(self) -> None:
        dialog = AddRoomDialog(self)
        while dialog.exec() == QDialog.Accepted:
            try:
                url, platform, name, quality = dialog.result_data()
                self.storage.add_room(platform, url, name, quality)
                self.load_rooms()
                self.manager.wake()
                return
            except UnsupportedPlatform as exc:
                QMessageBox.warning(dialog, "链接不支持", str(exc))
            except (sqlite3.Error, OSError, ValueError) as exc:
                message = "这个直播间已经在列表中" if "UNIQUE" in str(exc) else str(exc)
                QMessageBox.warning(dialog, "无法添加", message)

    def toggle_room(self, room_id: int, enabled: bool) -> None:
        room = self.rooms[room_id]
        room.enabled = enabled
        room.status = RoomStatus.OFFLINE if enabled else RoomStatus.PAUSED
        self.storage.update_room(room)
        if not enabled:
            self.manager.stop_recording(room_id)
        self.load_rooms()
        self.manager.wake()

    def remove_room(self, room_id: int) -> None:
        room = self.rooms[room_id]
        answer = QMessageBox.question(self, "删除直播间", f"确定删除“{room.display_name or room.url}”吗？\n已录制的视频不会被删除。")
        if answer != QMessageBox.Yes:
            return
        self.manager.stop_recording(room_id)
        self.storage.remove_room(room_id)
        self.load_rooms()

    def update_room_status(self, room_id: int, status_value: str, message: str) -> None:
        if room_id not in self.room_rows:
            return
        row = self.room_rows[room_id]
        status = RoomStatus(status_value)
        self.rooms[room_id].status = status
        self.rooms[room_id].last_message = message
        self._set_status_cell(row, self.rooms[room_id])
        self._set_action_cell(row, self.rooms[room_id])

    def on_recording_started(self, room_id: int, path: str, started_at: float) -> None:
        self.recording_started[room_id] = started_at
        row = self.room_rows.get(room_id)
        if row is not None:
            self.room_table.item(row, 2).setToolTip(path)

    def on_recording_finished(self, room_id: int, result: str, folder: str) -> None:
        self.recording_started.pop(room_id, None)
        self.load_history()

    def _refresh_elapsed(self) -> None:
        now = time.time()
        for room_id, started in list(self.recording_started.items()):
            row = self.room_rows.get(room_id)
            if row is None:
                continue
            elapsed = max(0, int(now - started))
            hours, remain = divmod(elapsed, 3600)
            minutes, seconds = divmod(remain, 60)
            self._set_status_cell(row, self.rooms[room_id], f"{hours:02d}:{minutes:02d}:{seconds:02d}")

    def update_counts(self, monitoring: int, recording: int) -> None:
        suffix = f" · {recording} 个正在录制" if recording else ""
        self.monitoring_label.setText(f"正在监测 {monitoring} 个直播间{suffix}")

    def load_settings(self) -> None:
        options = self.storage.load_options()
        self.output_edit.setText(str(options.output_dir))
        self.segment_spin.setValue(options.segment_minutes)
        self.interval_spin.setValue(options.check_interval)
        self.dy_cookie.setText(options.douyin_cookie)
        self.xhs_cookie.setText(options.xhs_cookie)

    def choose_output(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "选择视频保存目录", self.output_edit.text())
        if directory:
            self.output_edit.setText(directory)

    def save_settings(self) -> None:
        output = Path(self.output_edit.text()).expanduser()
        try:
            output.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.warning(self, "目录不可用", str(exc))
            return
        options = RecordingOptions(
            output, self.segment_spin.value(), self.interval_spin.value(),
            self.dy_cookie.text().strip(), self.xhs_cookie.text().strip(),
        )
        self.storage.save_options(options)
        self.manager.wake()
        QMessageBox.information(self, "设置已保存", "新的设置将在下次检测或录制时生效。")

    def load_history(self) -> None:
        history = self.storage.list_history()
        self.history_table.setRowCount(len(history))
        result_labels = {
            "recording": "录制中",
            "completed": "已完成",
            "saved": "已保存",
            "stopped": "已停止",
            "failed": "连接中断",
            "failed_no_output": "未生成文件",
            "recovered": "唤醒后恢复",
            "encrypted": "会员加密，无法录制",
        }
        for row, item in enumerate(history):
            try:
                platform = Platform(item["platform"]).label
            except ValueError:
                platform = item["platform"]
            files = json.loads(item.get("files_json") or "[]")
            existing_files = [path for value in files if (path := Path(value)).is_file()]
            files_created = item.get("files_created")
            result_label = result_labels.get(item["result"], item["result"])
            if files_created:
                result_label += f"（{files_created} 段）"
            if existing_files:
                location = str(existing_files[0].parent)
                location_tooltip = "\n".join(str(path) for path in existing_files)
            elif item["result"] == "recording":
                location = "正在等待生成文件…"
                location_tooltip = str(Path(item["file_path"]).parent)
            elif files_created == 0 or item["result"] == "failed_no_output":
                location = "未生成文件"
                location_tooltip = item.get("error") or "本次录制没有产生视频文件"
            elif files_created:
                location = "文件已移动或删除"
                location_tooltip = str(Path(item["file_path"]).parent)
            else:
                # Records made by versions before file tracking was added.
                location = str(Path(item["file_path"]).parent)
                location_tooltip = "旧版记录，无法确认本次实际生成的文件"
            values = [
                platform, item["anchor_name"], item["title"], item["started_at"].replace("T", " "),
                (item["ended_at"] or "—").replace("T", " "), result_label, location,
            ]
            for column, value in enumerate(values):
                cell = QTableWidgetItem(value)
                if column == 6:
                    cell.setToolTip(location_tooltip)
                self.history_table.setItem(row, column, cell)
            self.history_table.setRowHeight(row, 44)

    def set_tray_icon(self, tray_icon: QSystemTrayIcon) -> None:
        self.tray_icon = tray_icon

    def show_from_tray(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()

    def request_quit(self) -> None:
        if self.recording_started:
            answer = QMessageBox.question(
                self, "退出直播守候", "当前仍有直播正在录制。退出会安全停止录制，确定继续吗？",
            )
            if answer != QMessageBox.Yes:
                return
        self._quitting = True
        self.manager.shutdown()
        if self.tray_icon:
            self.tray_icon.hide()
        QApplication.instance().quit()

    def closeEvent(self, event) -> None:
        if self._quitting or not self.tray_icon or not self.tray_icon.isVisible():
            self.manager.shutdown()
            event.accept()
            return
        event.ignore()
        self.hide()
        if not self._tray_notice_shown:
            self.tray_icon.showMessage(
                "直播守候仍在运行",
                "窗口已缩到菜单栏，将继续监测和录制。点击菜单栏图标可重新打开。",
                QSystemTrayIcon.MessageIcon.Information,
                5000,
            )
            self._tray_notice_shown = True


APP_STYLE = """
QWidget { background: #FFFCF4; color: #17233F; font-family: -apple-system, "PingFang SC", "Microsoft YaHei"; font-size: 14px; }
QLabel { background: transparent; }
#sidebar { background: #FFF9EA; border-right: 1px solid #ECE8DD; }
#brand { color: #17233F; font-size: 19px; font-weight: 750; }
#privacy { color: #9A9EAA; padding: 8px; font-size: 12px; }
#navButton { text-align: left; color: #657087; background: transparent; border: 0; border-left: 4px solid transparent; padding: 13px 12px; border-radius: 3px; }
#navButton:hover { background: #FFF3C8; color: #17233F; }
#navButton:checked { background: transparent; color: #17233F; border-left: 4px solid #FFD83D; font-weight: 700; }
#pageTitle { font-size: 29px; font-weight: 750; }
#dialogTitle { font-size: 21px; font-weight: 700; }
#muted { color: #7A8499; }
#warning { color: #B66A12; background: #FFF5DC; border-radius: 7px; padding: 10px; }
#settingsPanel, #taskPanel { background: white; border: 1px solid #E1E3E8; border-radius: 11px; }
#panelTitle { background: white; border-bottom: 1px solid #ECEEF2; font-size: 19px; font-weight: 750; }
#panelHint { color: #8C93A2; background: #FCFCFB; border-top: 1px solid #ECEEF2; }
QPushButton { background: white; border: 1px solid #CDD2DB; padding: 8px 13px; border-radius: 8px; }
QPushButton:hover { border-color: #17233F; color: #17233F; }
QPushButton:checked { background: #FFF3C8; color: #17233F; border-color: #FFD83D; }
#primaryButton { background: #FFD83D; color: #17233F; border: none; padding: 11px 18px; font-weight: 700; }
#primaryButton:hover { background: #F3C925; color: #17233F; }
#dangerText { color: #D84A56; }
QLineEdit, QComboBox, QSpinBox { background: white; border: 1px solid #DDE1EA; padding: 9px 10px; border-radius: 7px; min-height: 20px; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus { border: 1px solid #E2B916; }
QTableWidget { background: white; border: none; alternate-background-color: white; selection-background-color: #FFF7DB; }
QHeaderView::section { background: #F1F3F8; color: #626D82; border: none; border-bottom: 1px solid #E3E6EE; padding: 11px; font-weight: 650; }
QTableWidget::item { padding: 14px; border-bottom: 1px solid #ECEEF2; }
QMessageBox, QDialog { background: #FFFCF4; }
"""


def apply_style(app: QApplication) -> None:
    app.setStyle("Fusion")
    app.setStyleSheet(APP_STYLE)
    font = QFont()
    font.setPointSize(13)
    app.setFont(font)
