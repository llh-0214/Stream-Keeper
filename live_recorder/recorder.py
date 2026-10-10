from __future__ import annotations

import asyncio
import os
import re
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import imageio_ffmpeg
from PySide6.QtCore import QObject, Signal

from .models import Platform, RecordingOptions, Room, RoomStatus, StreamInfo
from .power import SleepPreventer
from .resolver import ResolveError, resolve
from .storage import Storage


def safe_name(value: str, fallback: str = "直播") -> str:
    value = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "_", value).strip(" ._")
    return value[:80] or fallback


@dataclass
class ActiveRecording:
    process: subprocess.Popen
    history_id: int
    output_pattern: Path
    started_at: float
    stopping: bool = False
    recovering: bool = False


class RecorderManager(QObject):
    room_status = Signal(int, str, str)
    recording_started = Signal(int, str, float)
    recording_finished = Signal(int, str, str)
    counts_changed = Signal(int, int)
    history_changed = Signal()

    def __init__(self, storage: Storage):
        super().__init__()
        self.storage = storage
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._active: dict[int, ActiveRecording] = {}
        self._active_lock = threading.Lock()
        self._sequence_lock = threading.Lock()
        self._next_check: dict[int, float] = {}
        self._sleep_preventer = SleepPreventer()
        self._last_loop_at = time.time()

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._last_loop_at = time.time()
        self._sleep_preventer.start()
        self._thread = threading.Thread(target=self._monitor_loop, name="room-monitor", daemon=True)
        self._thread.start()

    def wake(self) -> None:
        self._next_check.clear()
        self._wake.set()

    def _emit_counts(self, rooms: list[Room] | None = None) -> None:
        rooms = rooms or self.storage.list_rooms()
        with self._active_lock:
            recording_count = len(self._active)
        self.counts_changed.emit(sum(room.enabled for room in rooms), recording_count)

    def _monitor_loop(self) -> None:
        while not self._stop.is_set():
            now_wall = time.time()
            if now_wall - self._last_loop_at > 8:
                self._recover_after_wake()
            self._last_loop_at = now_wall
            rooms = self.storage.list_rooms()
            options = self.storage.load_options()
            options.output_dir.mkdir(parents=True, exist_ok=True)
            now = time.monotonic()
            for room in rooms:
                if self._stop.is_set():
                    break
                if not room.enabled:
                    self.room_status.emit(room.id, RoomStatus.PAUSED.value, "已暂停自动检测")
                    continue
                with self._active_lock:
                    is_recording = room.id in self._active
                if is_recording or now < self._next_check.get(room.id, 0):
                    continue
                self._next_check[room.id] = now + max(15, options.check_interval)
                self._check_room(room, options)
            self._emit_counts(rooms)
            self._wake.wait(timeout=1.0)
            self._wake.clear()

    def _recover_after_wake(self) -> None:
        """Discard stale network processes and immediately resolve fresh URLs."""
        self._next_check.clear()
        with self._active_lock:
            recordings = list(self._active.values())
        for active in recordings:
            if active.process.poll() is not None:
                continue
            active.recovering = True
            try:
                if active.process.stdin:
                    active.process.stdin.write(b"q\n")
                    active.process.stdin.flush()
            except (BrokenPipeError, OSError):
                pass
            threading.Thread(target=self._force_stop_later, args=(active,), daemon=True).start()

    def _check_room(self, room: Room, options: RecordingOptions) -> None:
        self.room_status.emit(room.id, RoomStatus.CHECKING.value, "正在检测直播状态…")
        cookie = ""
        if room.platform is Platform.DOUYIN:
            cookie = options.douyin_cookie
        elif room.platform is Platform.XIAOHONGSHU:
            cookie = options.xhs_cookie
        try:
            info = asyncio.run(resolve(room.platform, room.url, room.quality, cookie))
            if not info.is_live:
                name = info.anchor_name if not room.display_name else room.display_name
                self.room_status.emit(room.id, RoomStatus.OFFLINE.value, f"{name} 暂未开播")
                return
            self.room_status.emit(room.id, RoomStatus.LIVE.value, "检测到开播，正在启动录制…")
            self._start_recording(room, info, options)
        except ResolveError as exc:
            self.room_status.emit(room.id, RoomStatus.ERROR.value, str(exc))
        except (RuntimeError, OSError, ValueError, TypeError) as exc:
            self.room_status.emit(room.id, RoomStatus.ERROR.value, f"检测异常：{exc}")

    def _start_recording(self, room: Room, info: StreamInfo, options: RecordingOptions) -> None:
        anchor = safe_name(room.display_name or info.anchor_name, room.platform.label + "主播")
        started_at = datetime.now()
        day_dir = options.output_dir / room.platform.label / anchor / started_at.strftime("%Y-%m-%d")
        day_dir.mkdir(parents=True, exist_ok=True)
        date_text = started_at.strftime("%Y%m%d")
        title = safe_name(info.title, "直播")
        segment_seconds = max(60, options.segment_minutes * 60)
        with self._sequence_lock:
            start_number = next_daily_sequence(day_dir, anchor, date_text)
        pattern = day_dir / f"{anchor}_{date_text}_%03d.mp4"
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        command = [
            ffmpeg, "-hide_banner", "-loglevel", "warning", "-y",
            "-rw_timeout", "20000000",
        ]
        if info.stream_url.startswith(("http://", "https://")):
            command += [
                "-reconnect", "1",
                "-reconnect_streamed", "1",
                "-reconnect_at_eof", "1",
                "-reconnect_on_network_error", "1",
                "-reconnect_on_http_error", "4xx,5xx",
                "-reconnect_delay_max", "10",
                "-reconnect_delay_total_max", "60",
            ]
        if info.headers:
            command += ["-headers", info.headers]
        command += [
            "-i", info.stream_url,
            "-map", "0:v?", "-map", "0:a?",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-f", "segment", "-segment_time", str(segment_seconds),
            "-segment_start_number", str(start_number),
            "-reset_timestamps", "1", "-segment_format", "mp4",
            "-segment_format_options", "movflags=+frag_keyframe+empty_moov",
            str(pattern),
        ]
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        kwargs: dict = {
            "stdin": subprocess.PIPE, "stdout": subprocess.DEVNULL,
            "stderr": subprocess.PIPE, "creationflags": flags,
        }
        if os.name != "nt":
            kwargs["start_new_session"] = True
        try:
            process = subprocess.Popen(command, **kwargs)
        except OSError as exc:
            self.room_status.emit(room.id, RoomStatus.ERROR.value, f"无法启动 FFmpeg：{exc}")
            return
        history_id = self.storage.start_history(room, anchor, info.title, pattern)
        active = ActiveRecording(process, history_id, pattern, time.time())
        with self._active_lock:
            self._active[room.id] = active
        self.room_status.emit(room.id, RoomStatus.RECORDING.value, f"正在录制：{title}")
        self.recording_started.emit(room.id, str(pattern), active.started_at)
        self._emit_counts()
        threading.Thread(
            target=self._watch_process, args=(room, active), name=f"ffmpeg-{room.id}", daemon=True
        ).start()

    def _watch_process(self, room: Room, active: ActiveRecording) -> None:
        stderr = active.process.stderr.read() if active.process.stderr else b""
        active.process.wait()
        return_code = active.process.returncode
        message = (stderr or b"").decode("utf-8", errors="replace")[-1200:].strip()
        if active.recovering:
            result = "recovered"
        elif active.stopping:
            result = "stopped"
        else:
            result = "completed" if return_code == 0 else "failed"
        error = "" if result in {"stopped", "completed", "recovered"} else message
        self.storage.finish_history(active.history_id, result, error)
        with self._active_lock:
            self._active.pop(room.id, None)
        if result == "failed":
            display = message.splitlines()[-1] if message else f"FFmpeg 返回 {return_code}"
            self.room_status.emit(room.id, RoomStatus.ERROR.value, f"直播流中断，正在重新连接：{display}")
        elif result == "recovered":
            self.room_status.emit(room.id, RoomStatus.CHECKING.value, "电脑已唤醒，正在恢复录制…")
        else:
            self.room_status.emit(room.id, RoomStatus.CHECKING.value, "录制流已结束，正在确认直播状态…")
        self.recording_finished.emit(room.id, result, str(active.output_pattern.parent))
        self.history_changed.emit()
        self._next_check[room.id] = time.monotonic() + (1 if active.recovering else 3)
        self._wake.set()
        self._emit_counts()

    def stop_recording(self, room_id: int) -> None:
        with self._active_lock:
            active = self._active.get(room_id)
        if not active or active.process.poll() is not None:
            return
        active.stopping = True
        try:
            if active.process.stdin:
                active.process.stdin.write(b"q\n")
                active.process.stdin.flush()
        except (BrokenPipeError, OSError):
            pass
        threading.Thread(target=self._force_stop_later, args=(active,), daemon=True).start()

    @staticmethod
    def _force_stop_later(active: ActiveRecording) -> None:
        try:
            active.process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            if os.name != "nt":
                try:
                    os.killpg(active.process.pid, signal.SIGTERM)
                    return
                except OSError:
                    pass
            active.process.terminate()

    def shutdown(self) -> None:
        self._stop.set()
        self._wake.set()
        with self._active_lock:
            room_ids = list(self._active)
        for room_id in room_ids:
            self.stop_recording(room_id)
        if self._thread:
            self._thread.join(timeout=3)
        self._sleep_preventer.stop()

    def active_started_at(self, room_id: int) -> float | None:
        with self._active_lock:
            active = self._active.get(room_id)
            return active.started_at if active else None


def next_daily_sequence(day_dir: Path, anchor: str, date_text: str) -> int:
    """Return the next sequence number for 主播名_年月日_序号.mp4."""
    expression = re.compile(rf"^{re.escape(anchor)}_{re.escape(date_text)}_(\d+)\.mp4$")
    largest = 0
    if day_dir.exists():
        for path in day_dir.iterdir():
            match = expression.match(path.name)
            if match:
                largest = max(largest, int(match.group(1)))
    return largest + 1
