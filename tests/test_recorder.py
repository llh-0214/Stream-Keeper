from live_recorder.recorder import new_recording_files, next_daily_sequence, recording_files, safe_name


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


def test_recording_files_only_returns_matching_segments(tmp_path):
    pattern = tmp_path / "主播_20261010_%03d.mp4"
    first = tmp_path / "主播_20261010_001.mp4"
    first.touch()
    (tmp_path / "其他文件.mp4").touch()
    assert recording_files(pattern) == [first]


def test_new_recording_files_excludes_files_from_earlier_session(tmp_path):
    pattern = tmp_path / "主播_20261010_%03d.mp4"
    first = tmp_path / "主播_20261010_001.mp4"
    second = tmp_path / "主播_20261010_002.mp4"
    first.touch()
    initial = frozenset(recording_files(pattern))
    second.touch()
    assert new_recording_files(pattern, initial) == [second]
