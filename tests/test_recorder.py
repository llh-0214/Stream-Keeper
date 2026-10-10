from live_recorder.recorder import next_daily_sequence, safe_name


def test_safe_name_removes_forbidden_characters():
    assert safe_name('主播/标题:*?"<>|') == "主播_标题"


def test_safe_name_has_fallback():
    assert safe_name("...") == "直播"


def test_next_daily_sequence_uses_existing_files(tmp_path):
    (tmp_path / "主播_20261010_001.mp4").touch()
    (tmp_path / "主播_20261010_003.mp4").touch()
    (tmp_path / "其他主播_20261010_099.mp4").touch()
    assert next_daily_sequence(tmp_path, "主播", "20261010") == 4


def test_next_daily_sequence_starts_at_one(tmp_path):
    assert next_daily_sequence(tmp_path, "主播", "20261010") == 1
