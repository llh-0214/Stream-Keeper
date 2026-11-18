from pathlib import Path
from types import SimpleNamespace

from live_recorder.models import Platform
from live_recorder.recorder import (
    ActiveRecording,
    build_ffmpeg_command,
    consolidate_recording_files,
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


def test_http_streams_allow_safe_inline_reconnect():
    assert should_reconnect_in_ffmpeg(
        Platform.DOUYIN,
        "https://pull.example/live.flv?expire=123",
    )
    assert should_reconnect_in_ffmpeg(
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


def test_douyin_command_normalizes_timestamps_and_rejects_corrupt_frames(tmp_path):
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
    assert "-reconnect" in command
    assert "-use_wallclock_as_timestamps" in command
    assert "aresample=async=1000:first_pts=0" in command
    assert command[command.index("-reconnect_on_http_error") + 1] == "5xx"
    assert "libx264" in command
    assert command[-1].endswith("主播_%03d.mp4")


def test_consolidate_reconnect_fragments_replaces_sources_after_success(tmp_path, monkeypatch):
    first = tmp_path / "主播_20261118_001.mp4"
    second = tmp_path / "主播_20261118_002.mp4"
    first.write_bytes(b"old-one")
    second.write_bytes(b"old-two")

    def fake_run(command, **_kwargs):
        output = command[-1].replace("%03d", "001")
        Path(output).write_bytes(b"merged")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("live_recorder.recorder.subprocess.run", fake_run)
    outputs = consolidate_recording_files([first, second], 1800, "ffmpeg")
    assert outputs == [first]
    assert first.read_bytes() == b"merged"
    assert not second.exists()
    assert not list(tmp_path.glob(".streamkeeper-merge-*"))
