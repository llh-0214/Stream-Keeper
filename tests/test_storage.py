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
    storage.finish_history(history_id, "completed")
    item = storage.list_history()[0]
    assert item["result"] == "completed"
    assert item["anchor_name"] == "主播"

