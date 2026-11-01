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
from .resolver import ProtectedStreamError, ResolveError, resolve
from .storage import Storage


def safe_name(value: str, fallback: str = "直播") -> str:
    value = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "_", value).strip(" ._")
    return value[:80] or fallback


def system_sleep_gap(
    last_wall: float,
    last_monotonic: float,
    now_wall: float,
    now_monotonic: float,
) -> float:
    """Estimate time spent asleep without mistaking slow network work for a wake."""
    wall_elapsed = now_wall - last_wall
    monotonic_elapsed = now_monotonic - last_monotonic
    return max(0.0, wall_elapsed - monotonic_elapsed)


def should_reconnect_in_ffmpeg(platform: Platform, stream_url: str) -> bool:
    """Avoid byte-range reconnects that can splice a broken live FLV stream."""
    clean_url = stream_url.lower().split("?", 1)[0]
    return (
        stream_url.startswith(("http://", "https://"))
        and platform is not Platform.DOUYIN
        and not clean_url.endswith(".flv")
    )


def video_output_args(platform: Platform, segment_seconds: int) -> list[str]:
    """Decode Douyin video so corrupt/encrypted frames stop the recording quickly."""
    if platform is not Platform.DOUYIN:
        return ["-c:v", "copy"]
    keyframes = f"expr:gte(t,n_forced*{segment_seconds})"
    return [
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
        "-g",
        "60",
        "-force_key_frames",
        keyframes,
    ]


def build_ffmpeg_command(
    ffmpeg: str,
    platform: Platform,
    stream_url: str,
    headers: str,
    pattern: Path,
    start_number: int,
    segment_seconds: int,
) -> list[str]:
    """Build a recording command with platform-safe reconnect and validation rules."""
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "warning",
        "-y",
        "-rw_timeout",
        "20000000",
        "-fflags",
        "+discardcorrupt",
        "-err_detect",
        "explode",
        "-xerror",
    ]
    if should_reconnect_in_ffmpeg(platform, stream_url):
        command += [
            "-reconnect",
            "1",
            "-reconnect_streamed",
            "1",
            "-reconnect_at_eof",
            "1",
            "-reconnect_on_network_error",
            "1",
            "-reconnect_on_http_error",
            "4xx,5xx",
            "-reconnect_delay_max",
            "10",
            "-reconnect_delay_total_max",
            "60",
        ]
    if headers:
        command += ["-headers", headers]
    command += ["-i", stream_url, "-map", "0:v?", "-map", "0:a?"]
    command += video_output_args(platform, segment_seconds)
    command += [
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-f",
        "segment",
        "-segment_time",
        str(segment_seconds),
        "-segment_start_number",
        str(start_number),
        "-reset_timestamps",
        "1",
        "-segment_format",
        "mp4",
        "-segment_format_options",
        "movflags=+frag_keyframe+empty_moov",
        str(pattern),
    ]
    return command


@dataclass
class ActiveRecording:
    process: subprocess.Popen
    history_id: int
    output_pattern: Path
    started_at: float
    initial_files: frozenset[Path]
    used_fallback: bool = False
    has_fallback: bool = False
    validated: bool = False
    stopping: bool = False
    recovering: bool = False
    no_data_timeout: bool = False
    timeout_message: str = ""


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
        self._fallback_next: set[int] = set()
        self._sleep_preventer = SleepPreventer()
        self._last_wall_at = time.time()
        self._last_monotonic_at = time.monotonic()

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._last_wall_at = time.time()
        self._last_monotonic_at = time.monotonic()
        self._sleep_preventer.start()
        self._thread = threading.Thread(target=self._monitor_loop, name="room-monitor", daemon=True)
        self._thread.start()

    def wake(self) -> None:
        self._next_check.clear()
        self._wake.set()

    def _emit_counts(self, rooms: list[Room] | None = None) -> None:
        rooms = rooms or self.storage.list_rooms()
        with self._active_lock:
            recording_count = sum(active.validated for active in self._active.values())
        self.counts_changed.emit(sum(room.enabled for room in rooms), recording_count)

    def _monitor_loop(self) -> None:
        while not self._stop.is_set():
            now_wall = time.time()
            now_monotonic = time.monotonic()
            if (
                system_sleep_gap(
                    self._last_wall_at,
                    self._last_monotonic_at,
                    now_wall,
                    now_monotonic,
                )
                > 8
            ):
                self._recover_after_wake()
            self._last_wall_at = now_wall
            self._last_monotonic_at = now_monotonic
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
        except ProtectedStreamError as exc:
            self.room_status.emit(room.id, RoomStatus.PROTECTED.value, str(exc))
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
        initial_files = frozenset(recording_files(pattern))
        use_fallback = room.id in self._fallback_next and bool(info.fallback_urls)
        self._fallback_next.discard(room.id)
        stream_url = info.fallback_urls[0] if use_fallback else info.stream_url
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        command = build_ffmpeg_command(
            ffmpeg,
            room.platform,
            stream_url,
            info.headers,
            pattern,
            start_number,
            segment_seconds,
        )
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        kwargs: dict = {
            "stdin": subprocess.PIPE,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.PIPE,
            "creationflags": flags,
        }
        if os.name != "nt":
            kwargs["start_new_session"] = True
        try:
            process = subprocess.Popen(command, **kwargs)
        except OSError as exc:
            self.room_status.emit(room.id, RoomStatus.ERROR.value, f"无法启动 FFmpeg：{exc}")
            return
        history_id = self.storage.start_history(room, anchor, info.title, pattern)
        active = ActiveRecording(
            process,
            history_id,
            pattern,
            time.time(),
            initial_files,
            used_fallback=use_fallback,
            has_fallback=bool(info.fallback_urls),
        )
        with self._active_lock:
            self._active[room.id] = active
        self.room_status.emit(room.id, RoomStatus.LIVE.value, f"正在连接视频流：{title}")
        self._emit_counts()
        threading.Thread(
            target=self._watch_output,
            args=(room, active, title),
            name=f"output-{room.id}",
            daemon=True,
        ).start()
        threading.Thread(
            target=self._watch_process, args=(room, active), name=f"ffmpeg-{room.id}", daemon=True
        ).start()

    def _watch_output(self, room: Room, active: ActiveRecording, title: str) -> None:
        """Confirm real output and stop stale FFmpeg processes so a fresh URL is resolved."""
        startup_deadline = time.monotonic() + 45
        last_growth = time.monotonic()
        last_bytes = 0
        while not self._stop.is_set() and active.process.poll() is None:
            files = new_recording_files(active.output_pattern, active.initial_files)
            total_bytes = recording_size(files)
            now = time.monotonic()
            if total_bytes > last_bytes:
                last_bytes = total_bytes
                last_growth = now
            if total_bytes > 0 and not active.validated:
                active.validated = True
                self._fallback_next.discard(room.id)
                self.room_status.emit(room.id, RoomStatus.RECORDING.value, f"正在录制：{title}")
                self.recording_started.emit(room.id, str(active.output_pattern), active.started_at)
                self._emit_counts()
            if not active.validated and now >= startup_deadline:
                active.no_data_timeout = True
                active.timeout_message = "连接视频流超时：45 秒内没有生成有效视频文件"
                self._terminate_active(active)
                return
            if active.validated and now - last_growth >= 90:
                active.no_data_timeout = True
                active.timeout_message = "直播流连续 90 秒没有新数据"
                self._terminate_active(active)
                return
            time.sleep(1)

    def _watch_process(self, room: Room, active: ActiveRecording) -> None:
        stderr = active.process.stderr.read() if active.process.stderr else b""
        active.process.wait()
        return_code = active.process.returncode
        message = (stderr or b"").decode("utf-8", errors="replace")[-1200:].strip()
        files = new_recording_files(active.output_pattern, active.initial_files)
        result = recording_result(active, return_code, files)
        if active.no_data_timeout:
            message = "\n".join(part for part in (active.timeout_message, message) if part)
        error = "" if result in {"stopped", "completed", "recovered"} else message
        self.storage.finish_history(active.history_id, result, error, files)
        with self._active_lock:
            self._active.pop(room.id, None)
        if result == "failed_no_output":
            if active.has_fallback and not active.used_fallback:
                self._fallback_next.add(room.id)
            display = active.timeout_message or (message.splitlines()[-1] if message else "没有收到视频数据")
            self.room_status.emit(room.id, RoomStatus.ERROR.value, f"未生成文件，正在重新连接：{display}")
        elif result == "saved":
            self.room_status.emit(
                room.id,
                RoomStatus.CHECKING.value,
                f"已保存 {len(files)} 段视频，正在确认直播状态…",
            )
        elif result == "failed":
            display = message.splitlines()[-1] if message else f"FFmpeg 返回 {return_code}"
            self.room_status.emit(room.id, RoomStatus.ERROR.value, f"直播流中断，正在重新连接：{display}")
        elif result == "recovered":
            self.room_status.emit(room.id, RoomStatus.CHECKING.value, "电脑已唤醒，正在恢复录制…")
        else:
            self.room_status.emit(room.id, RoomStatus.CHECKING.value, "录制流已结束，正在确认直播状态…")
        self.recording_finished.emit(room.id, result, str(active.output_pattern.parent))
        self.history_changed.emit()
        retry_quickly = active.recovering or result in {"saved", "failed", "failed_no_output"}
        self._next_check[room.id] = time.monotonic() + (1 if retry_quickly else 3)
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
    def _terminate_active(active: ActiveRecording) -> None:
        try:
            if active.process.stdin:
                active.process.stdin.write(b"q\n")
                active.process.stdin.flush()
        except (BrokenPipeError, OSError):
            pass
        threading.Thread(target=RecorderManager._force_stop_later, args=(active,), daemon=True).start()

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


def recording_files(pattern: Path) -> list[Path]:
    """Return files produced by an FFmpeg numeric segment pattern."""
    glob_name = re.sub(r"%0?\d*d", "*", pattern.name)
    return sorted(pattern.parent.glob(glob_name))


def new_recording_files(pattern: Path, initial_files: frozenset[Path]) -> list[Path]:
    return [path for path in recording_files(pattern) if path not in initial_files]


def recording_size(files: list[Path]) -> int:
    total = 0
    for path in files:
        try:
            total += path.stat().st_size
        except OSError:
            continue
    return total


def recording_result(active: ActiveRecording, return_code: int | None, files: list[Path]) -> str:
    """Describe the user-visible outcome without treating saved video as a failed recording."""
    if active.recovering:
        return "recovered"
    if active.stopping:
        return "stopped"
    if not files:
        return "failed_no_output"
    if return_code == 0:
        return "completed"
    return "saved"
