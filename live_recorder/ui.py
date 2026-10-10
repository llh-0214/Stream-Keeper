from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
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
    RoomStatus.OFFLINE.value: "#7A8499",
    RoomStatus.LIVE.value: "#35B57B",
    RoomStatus.RECORDING.value: "#FF5A63",
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
        help_text = QLabel("粘贴抖音或小红书的直播间/分享链接。开播后会自动开始录制。")
        help_text.setWordWrap(True)
        help_text.setObjectName("muted")
        layout.addWidget(title)
        layout.addWidget(help_text)
        form = QFormLayout()
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("https://live.douyin.com/... 或 https://xhslink.com/...")
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


class StatCard(QFrame):
    def __init__(self, caption: str, value: str, accent: str):
        super().__init__()
        self.setObjectName("statCard")
        layout = QVBoxLayout(self)
        dot = QLabel("●")
        dot.setStyleSheet(f"color:{accent}; font-size:16px")
        self.value = QLabel(value)
        self.value.setObjectName("statValue")
        label = QLabel(caption)
        label.setObjectName("muted")
        top = QHBoxLayout()
        top.addWidget(self.value)
        top.addStretch()
        top.addWidget(dot)
        layout.addLayout(top)
        layout.addWidget(label)


class MainWindow(QMainWindow):
    def __init__(self, storage: Storage, manager: RecorderManager):
        super().__init__()
        self.storage = storage
        self.manager = manager
        self.rooms: dict[int, Room] = {}
        self.room_rows: dict[int, int] = {}
        self.recording_started: dict[int, float] = {}
        self.setWindowTitle("直播守候 · 抖音与小红书录制")
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
        sidebar.setFixedWidth(218)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(20, 24, 20, 20)
        brand = QLabel("◉  直播守候")
        brand.setObjectName("brand")
        subtitle = QLabel("STREAM KEEPER")
        subtitle.setObjectName("brandSub")
        side.addWidget(brand)
        side.addWidget(subtitle)
        side.addSpacing(28)
        self.nav_buttons: list[QPushButton] = []
        for label, index in [("⌂  录制任务", 0), ("◷  历史记录", 1), ("⚙  偏好设置", 2)]:
            button = QPushButton(label)
            button.setObjectName("navButton")
            button.setCheckable(True)
            button.clicked.connect(lambda checked=False, i=index: self._navigate(i))
            side.addWidget(button)
            self.nav_buttons.append(button)
        self.nav_buttons[0].setChecked(True)
        side.addStretch()
        privacy = QLabel("所有配置和历史记录\n只保存在这台电脑")
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
        layout, header = self._page_header("录制任务", "添加直播间，程序会自动等待并在开播后录制")
        add = QPushButton("＋ 添加直播间")
        add.setObjectName("primaryButton")
        add.clicked.connect(self.add_room)
        header.addWidget(add)

        cards = QHBoxLayout()
        self.monitor_card = StatCard("正在监测", "0", "#665CF6")
        self.recording_card = StatCard("正在录制", "0", "#FF5A63")
        self.path_card = StatCard("保存格式", "MP4", "#35B57B")
        cards.addWidget(self.monitor_card)
        cards.addWidget(self.recording_card)
        cards.addWidget(self.path_card)
        layout.addSpacing(18)
        layout.addLayout(cards)
        layout.addSpacing(22)

        self.room_table = QTableWidget(0, 7)
        self.room_table.setHorizontalHeaderLabels(["平台 / 名称", "直播间", "状态", "录制时长", "画质", "自动监测", "操作"])
        self.room_table.verticalHeader().setVisible(False)
        self.room_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.room_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.room_table.setShowGrid(False)
        self.room_table.setAlternatingRowColors(True)
        self.room_table.setMinimumHeight(330)
        header_view = self.room_table.horizontalHeader()
        header_view.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header_view.setSectionResizeMode(1, QHeaderView.Stretch)
        header_view.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header_view.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header_view.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        header_view.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        header_view.setSectionResizeMode(6, QHeaderView.ResizeToContents)
        layout.addWidget(self.room_table, 1)
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
        platform_item = QTableWidgetItem(f"{room.platform.label}\n{label}")
        platform_item.setData(Qt.UserRole, room.id)
        self.room_table.setItem(row, 0, platform_item)
        url_item = QTableWidgetItem(room.url)
        url_item.setToolTip(room.url)
        self.room_table.setItem(row, 1, url_item)
        status = QTableWidgetItem(room.status.label)
        status.setForeground(QColor(STATUS_COLORS[room.status.value]))
        status.setToolTip(room.last_message)
        self.room_table.setItem(row, 2, status)
        self.room_table.setItem(row, 3, QTableWidgetItem("—"))
        quality_labels = {"OD": "原画", "UHD": "超清", "HD": "高清", "SD": "标清", "LD": "流畅"}
        self.room_table.setItem(row, 4, QTableWidgetItem(quality_labels.get(room.quality, room.quality)))
        toggle = QPushButton("已开启" if room.enabled else "已暂停")
        toggle.setCheckable(True)
        toggle.setChecked(room.enabled)
        toggle.clicked.connect(lambda checked=False, rid=room.id: self.toggle_room(rid, checked))
        self.room_table.setCellWidget(row, 5, toggle)
        actions = QWidget()
        action_layout = QHBoxLayout(actions)
        action_layout.setContentsMargins(2, 2, 2, 2)
        stop = QPushButton("停止")
        stop.setObjectName("dangerText")
        stop.clicked.connect(lambda checked=False, rid=room.id: self.manager.stop_recording(rid))
        remove = QPushButton("删除")
        remove.clicked.connect(lambda checked=False, rid=room.id: self.remove_room(rid))
        action_layout.addWidget(stop)
        action_layout.addWidget(remove)
        self.room_table.setCellWidget(row, 6, actions)
        self.room_table.setRowHeight(row, 58)

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
        item = QTableWidgetItem(status.label)
        item.setForeground(QColor(STATUS_COLORS[status_value]))
        item.setToolTip(message)
        self.room_table.setItem(row, 2, item)
        self.rooms[room_id].status = status
        self.rooms[room_id].last_message = message

    def on_recording_started(self, room_id: int, path: str, started_at: float) -> None:
        self.recording_started[room_id] = started_at
        row = self.room_rows.get(room_id)
        if row is not None:
            self.room_table.item(row, 3).setToolTip(path)

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
            self.room_table.setItem(row, 3, QTableWidgetItem(f"{hours:02d}:{minutes:02d}:{seconds:02d}"))

    def update_counts(self, monitoring: int, recording: int) -> None:
        self.monitor_card.value.setText(str(monitoring))
        self.recording_card.value.setText(str(recording))

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
        result_labels = {"recording": "录制中", "completed": "已完成", "stopped": "已停止", "failed": "失败"}
        for row, item in enumerate(history):
            platform = "抖音" if item["platform"] == Platform.DOUYIN.value else "小红书"
            values = [
                platform, item["anchor_name"], item["title"], item["started_at"].replace("T", " "),
                (item["ended_at"] or "—").replace("T", " "), result_labels.get(item["result"], item["result"]),
                str(Path(item["file_path"]).parent),
            ]
            for column, value in enumerate(values):
                cell = QTableWidgetItem(value)
                if column == 6:
                    cell.setToolTip(value)
                self.history_table.setItem(row, column, cell)
            self.history_table.setRowHeight(row, 44)

    def closeEvent(self, event) -> None:
        if self.recording_started:
            answer = QMessageBox.question(
                self, "退出直播守候", "当前仍有直播正在录制。退出会安全停止录制，确定继续吗？",
            )
            if answer != QMessageBox.Yes:
                event.ignore()
                return
        self.manager.shutdown()
        event.accept()


APP_STYLE = """
QWidget { background: #F6F7FB; color: #1C2434; font-family: -apple-system, "PingFang SC", "Microsoft YaHei"; font-size: 14px; }
QLabel { background: transparent; }
#sidebar { background: #171B2C; }
#brand { color: white; font-size: 21px; font-weight: 700; }
#brandSub { color: #777F9C; font-size: 10px; letter-spacing: 2px; }
#privacy { color: #777F9C; background: #20263B; border-radius: 10px; padding: 12px; font-size: 12px; }
#navButton { text-align: left; color: #AAB0C3; background: transparent; border: 0; padding: 12px 14px; border-radius: 9px; }
#navButton:hover { background: #22283D; color: white; }
#navButton:checked { background: #665CF6; color: white; font-weight: 600; }
#pageTitle { font-size: 27px; font-weight: 750; }
#dialogTitle { font-size: 21px; font-weight: 700; }
#muted { color: #7A8499; }
#warning { color: #B66A12; background: #FFF5DC; border-radius: 7px; padding: 10px; }
#statCard, #settingsPanel { background: white; border: 1px solid #E8EAF1; border-radius: 13px; }
#statValue { font-size: 25px; font-weight: 750; }
QPushButton { background: white; border: 1px solid #DDE1EA; padding: 8px 13px; border-radius: 8px; }
QPushButton:hover { border-color: #665CF6; color: #5147E5; }
QPushButton:checked { background: #EEF0FF; color: #5147E5; border-color: #C9C5FF; }
#primaryButton { background: #665CF6; color: white; border: none; padding: 10px 17px; font-weight: 650; }
#primaryButton:hover { background: #554AE7; color: white; }
#dangerText { color: #D84A56; }
QLineEdit, QComboBox, QSpinBox { background: white; border: 1px solid #DDE1EA; padding: 9px 10px; border-radius: 7px; min-height: 20px; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus { border: 1px solid #665CF6; }
QTableWidget { background: white; border: 1px solid #E8EAF1; border-radius: 12px; alternate-background-color: #FAFBFD; selection-background-color: #EEF0FF; }
QHeaderView::section { background: #F1F3F8; color: #626D82; border: none; border-bottom: 1px solid #E3E6EE; padding: 11px; font-weight: 650; }
QTableWidget::item { padding: 8px; border-bottom: 1px solid #EFF1F5; }
QMessageBox, QDialog { background: #F6F7FB; }
"""


def apply_style(app: QApplication) -> None:
    app.setStyle("Fusion")
    app.setStyleSheet(APP_STYLE)
    font = QFont()
    font.setPointSize(13)
    app.setFont(font)
