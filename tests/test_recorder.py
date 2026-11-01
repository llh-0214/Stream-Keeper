from live_recorder.models import Platform
from live_recorder.recorder import (
    ActiveRecording,
    build_ffmpeg_command,
    new_recording_files,
    next_daily_sequence,
    recording_files,
    recording_result,
    safe_name,
    should_reconnect_in_ffmpeg,
    system_sleep_gap,
    video_output_args,
)


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


def test_nonzero_exit_with_saved_files_is_not_reported_as_failure(tmp_path):
    video = tmp_path / "主播_20261014_001.mp4"
    video.touch()
    active = ActiveRecording(None, 1, tmp_path / "主播_20261014_%03d.mp4", 0, frozenset())
    assert recording_result(active, 1, [video]) == "saved"


def test_nonzero_exit_without_files_is_reported_as_no_output(tmp_path):
    active = ActiveRecording(None, 1, tmp_path / "主播_20261014_%03d.mp4", 0, frozenset())
    assert recording_result(active, 1, []) == "failed_no_output"


def test_slow_network_work_is_not_mistaken_for_system_sleep():
    assert system_sleep_gap(100.0, 40.0, 125.0, 65.0) == 0.0


def test_system_sleep_is_detected_from_clock_drift():
    assert system_sleep_gap(100.0, 40.0, 225.0, 45.0) == 120.0


def test_clock_rollback_does_not_trigger_sleep_recovery():
    assert system_sleep_gap(100.0, 40.0, 90.0, 45.0) == 0.0


def test_douyin_flv_uses_application_level_reconnect():
    assert not should_reconnect_in_ffmpeg(
        Platform.DOUYIN,
        "https://pull.example/live.flv?expire=123",
    )
    assert not should_reconnect_in_ffmpeg(
        Platform.GENERIC,
        "https://pull.example/live.flv?expire=123",
    )
    assert should_reconnect_in_ffmpeg(
        Platform.GENERIC,
        "https://pull.example/live.m3u8?expire=123",
    )


def test_douyin_video_is_decoded_and_reencoded_for_validation():
    args = video_output_args(Platform.DOUYIN, 1800)
    assert "libx264" in args
    assert "expr:gte(t,n_forced*1800)" in args
    assert video_output_args(Platform.XIAOHONGSHU, 1800) == ["-c:v", "copy"]


def test_douyin_command_rejects_corrupt_frames_and_does_not_inline_reconnect(tmp_path):
    command = build_ffmpeg_command(
        "ffmpeg",
        Platform.DOUYIN,
        "https://pull.example/live.flv",
        "",
        tmp_path / "主播_%03d.mp4",
        7,
        1800,
    )
    assert "-xerror" in command
    assert "explode" in command
    assert "-reconnect" not in command
    assert "libx264" in command
    assert command[-1].endswith("主播_%03d.mp4")
