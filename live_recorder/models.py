from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class Platform(str, Enum):
    DOUYIN = "douyin"
    XIAOHONGSHU = "xiaohongshu"
    BILIBILI = "bilibili"
    GENERIC = "generic"

    @property
    def label(self) -> str:
        return {
            self.DOUYIN: "抖音",
            self.XIAOHONGSHU: "小红书",
            self.BILIBILI: "哔哩哔哩",
            self.GENERIC: "通用直播流",
        }[self]


class RoomStatus(str, Enum):
    PAUSED = "paused"
    CHECKING = "checking"
    OFFLINE = "offline"
    LIVE = "live"
    RECORDING = "recording"
    ERROR = "error"

    @property
    def label(self) -> str:
        return {
            self.PAUSED: "已暂停",
            self.CHECKING: "检测中",
            self.OFFLINE: "未开播",
            self.LIVE: "已开播",
            self.RECORDING: "录制中",
            self.ERROR: "出错",
        }[self]


@dataclass(slots=True)
class Room:
    id: int
    platform: Platform
    url: str
    display_name: str = ""
    enabled: bool = True
    quality: str = "OD"
    status: RoomStatus = RoomStatus.PAUSED
    last_message: str = ""


@dataclass(slots=True)
class StreamInfo:
    platform: Platform
    url: str
    anchor_name: str
    title: str
    is_live: bool
    stream_url: str = ""
    headers: str = ""


@dataclass(slots=True)
class RecordingOptions:
    output_dir: Path
    segment_minutes: int = 30
    check_interval: int = 60
    douyin_cookie: str = ""
    xhs_cookie: str = ""
