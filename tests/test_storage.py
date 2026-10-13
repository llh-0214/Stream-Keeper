from datetime import datetime
from pathlib import Path

from live_recorder.models import Platform, RecordingOptions
from live_recorder.storage import Storage


def test_room_and_settings_round_trip(tmp_path: Path):
    storage = Storage(tmp_path)
    room = storage.add_room(Platform.DOUYIN, "https://live.douyin.com/123", "测试", "HD")
    assert room.id > 0
    loaded = storage.list_rooms()
    assert loaded[0].display_name == "测试"
    assert loaded[0].quality == "HD"

    options = RecordingOptions(tmp_path / "videos", 15, 45, "dy-cookie", "xhs-cookie")
    storage.save_options(options)
    reloaded = storage.load_options()
    assert reloaded.segment_minutes == 15
    assert reloaded.check_interval == 45
    assert reloaded.output_dir == tmp_path / "videos"


def test_history_round_trip(tmp_path: Path):
    storage = Storage(tmp_path)
    room = storage.add_room(Platform.XIAOHONGSHU, "https://xhslink.com/abc")
    history_id = storage.start_history(room, "主播", "标题", tmp_path / "video.mp4")
    video = tmp_path / "video_001.mp4"
    video.touch()
    storage.finish_history(history_id, "completed", files=[video])
    item = storage.list_history()[0]
    assert item["result"] == "completed"
    assert item["anchor_name"] == "主播"
    assert item["files_created"] == 1
    assert str(video) in item["files_json"]


def test_legacy_history_backfill_distinguishes_same_day_sessions(tmp_path: Path):
    storage = Storage(tmp_path / "data")
    room = storage.add_room(Platform.DOUYIN, "https://live.douyin.com/legacy")
    pattern = tmp_path / "主播_20261012_%03d.mp4"
    old_file = tmp_path / "主播_20261012_001.mp4"
    old_file.touch()
    stamp = datetime.now().timestamp()
    with storage._connect() as db:
        db.execute(
            "INSERT INTO history(room_id,platform,anchor_name,file_path,started_at,ended_at,result) "
            "VALUES(?,?,?,?,?,?,?)",
            (
                room.id, room.platform.value, "主播", str(pattern),
                datetime.fromtimestamp(stamp + 60).isoformat(timespec="seconds"),
                datetime.fromtimestamp(stamp + 120).isoformat(timespec="seconds"), "failed",
            ),
        )
        Storage._backfill_history_files(db)
    item = storage.list_history()[0]
    assert item["files_created"] == 0
    assert item["files_json"] == "[]"
