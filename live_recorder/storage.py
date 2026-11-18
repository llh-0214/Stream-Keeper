from __future__ import annotations

import json
import os
import platform
import re
import sqlite3
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import Platform, RecordingOptions, Room, RoomStatus


def default_data_dir() -> Path:
    override = os.environ.get("STREAM_KEEPER_DATA_DIR")
    if override:
        return Path(override).expanduser()
    system = platform.system()
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "StreamKeeper"
    if system == "Windows":
        return Path(os.environ.get("APPDATA", Path.home())) / "StreamKeeper"
    return Path.home() / ".local" / "share" / "stream-keeper"


class Storage:
    def __init__(self, data_dir: Path | None = None):
        self.data_dir = data_dir or default_data_dir()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "stream_keeper.db"
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS rooms (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    platform TEXT NOT NULL,
                    url TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    quality TEXT NOT NULL DEFAULT 'OD',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    room_id INTEGER,
                    platform TEXT NOT NULL,
                    anchor_name TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    file_path TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    result TEXT NOT NULL DEFAULT 'recording',
                    error TEXT NOT NULL DEFAULT '',
                    files_created INTEGER,
                    files_json TEXT,
                    FOREIGN KEY(room_id) REFERENCES rooms(id) ON DELETE SET NULL
                );
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
            columns = {row[1] for row in db.execute("PRAGMA table_info(history)").fetchall()}
            if "files_created" not in columns:
                db.execute("ALTER TABLE history ADD COLUMN files_created INTEGER")
            if "files_json" not in columns:
                db.execute("ALTER TABLE history ADD COLUMN files_json TEXT")
            self._backfill_history_files(db)
            # v0.2.1 labelled a recording as failed whenever FFmpeg ended with a
            # network error, even if valid segments had already been saved.
            db.execute(
                "UPDATE history SET result='saved' WHERE result='failed' AND COALESCE(files_created, 0) > 0"
            )

    @staticmethod
    def _backfill_history_files(db: sqlite3.Connection) -> None:
        """Attribute legacy segment files to their recording by modification time."""
        rows = db.execute(
            "SELECT id,file_path,started_at,ended_at FROM history "
            "WHERE files_json IS NULL AND ended_at IS NOT NULL"
        ).fetchall()
        for row in rows:
            pattern = Path(row["file_path"])
            glob_name = re.sub(r"%0?\d*d", "*", pattern.name)
            try:
                started = datetime.fromisoformat(row["started_at"]).timestamp() - 5
                ended = datetime.fromisoformat(row["ended_at"]).timestamp() + 30
                files = [
                    path
                    for path in pattern.parent.glob(glob_name)
                    if started <= path.stat().st_mtime <= ended
                ]
            except (OSError, ValueError):
                files = []
            file_strings = [str(path) for path in sorted(files)]
            db.execute(
                "UPDATE history SET files_created=?,files_json=? WHERE id=?",
                (len(file_strings), json.dumps(file_strings, ensure_ascii=False), row["id"]),
            )

    def list_rooms(self) -> list[Room]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM rooms ORDER BY id").fetchall()
        return [
            Room(
                id=row["id"],
                platform=Platform(row["platform"]),
                url=row["url"],
                display_name=row["display_name"],
                enabled=bool(row["enabled"]),
                quality=row["quality"],
                status=RoomStatus.OFFLINE if row["enabled"] else RoomStatus.PAUSED,
            )
            for row in rows
        ]

    def add_room(self, platform: Platform, url: str, display_name: str = "", quality: str = "OD") -> Room:
        with self._connect() as db:
            cursor = db.execute(
                "INSERT INTO rooms(platform,url,display_name,quality) VALUES(?,?,?,?)",
                (platform.value, url, display_name.strip(), quality),
            )
            room_id = int(cursor.lastrowid)
        return Room(room_id, platform, url, display_name.strip(), True, quality, RoomStatus.OFFLINE)

    def update_room(self, room: Room) -> None:
        with self._connect() as db:
            db.execute(
                "UPDATE rooms SET display_name=?,enabled=?,quality=? WHERE id=?",
                (room.display_name, int(room.enabled), room.quality, room.id),
            )

    def remove_room(self, room_id: int) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM rooms WHERE id=?", (room_id,))

    def get_setting(self, key: str, default: Any = None) -> Any:
        with self._connect() as db:
            row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        if not row:
            return default
        try:
            return json.loads(row["value"])
        except json.JSONDecodeError:
            return row["value"]

    def set_setting(self, key: str, value: Any) -> None:
        payload = json.dumps(value, ensure_ascii=False)
        with self._connect() as db:
            db.execute(
                "INSERT INTO settings(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, payload),
            )

    def load_options(self) -> RecordingOptions:
        default_output = self.data_dir / "recordings"
        return RecordingOptions(
            output_dir=Path(self.get_setting("output_dir", str(default_output))),
            segment_minutes=int(self.get_setting("segment_minutes", 30)),
            check_interval=int(self.get_setting("check_interval", 60)),
            douyin_cookie=str(self.get_setting("douyin_cookie", "")),
            xhs_cookie=str(self.get_setting("xhs_cookie", "")),
        )

    def save_options(self, options: RecordingOptions) -> None:
        for key, value in asdict(options).items():
            self.set_setting(key, str(value) if isinstance(value, Path) else value)

    def start_history(self, room: Room, anchor: str, title: str, path: Path) -> int:
        with self._connect() as db:
            cursor = db.execute(
                "INSERT INTO history(room_id,platform,anchor_name,title,file_path,started_at,files_created,files_json) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (
                    room.id,
                    room.platform.value,
                    anchor,
                    title,
                    str(path),
                    datetime.now().isoformat(timespec="seconds"),
                    0,
                    "[]",
                ),
            )
            return int(cursor.lastrowid)

    def finish_history(
        self,
        history_id: int,
        result: str,
        error: str = "",
        files: list[Path] | None = None,
    ) -> None:
        file_strings = [str(path) for path in (files or [])]
        with self._connect() as db:
            db.execute(
                "UPDATE history SET ended_at=?,result=?,error=?,files_created=?,files_json=? WHERE id=?",
                (
                    datetime.now().isoformat(timespec="seconds"),
                    result,
                    error,
                    len(file_strings),
                    json.dumps(file_strings, ensure_ascii=False),
                    history_id,
                ),
            )

    def replace_history_files(self, history_ids: list[int], files: list[Path]) -> None:
        """Point reconnect history rows at the consolidated final files."""
        if not history_ids:
            return
        file_strings = [str(path) for path in files]
        payload = json.dumps(file_strings, ensure_ascii=False)
        placeholders = ",".join("?" for _ in history_ids)
        with self._connect() as db:
            db.execute(
                f"UPDATE history SET result='completed',error='',files_created=?,files_json=? "
                f"WHERE id IN ({placeholders})",
                (len(file_strings), payload, *history_ids),
            )

    def list_history(self, limit: int = 300) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM history ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]
