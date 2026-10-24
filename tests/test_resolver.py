import json

from live_recorder.resolver import _is_encrypted_douyin_stream, _pick_quality


def test_douyin_prefers_flv_and_keeps_hls_as_fallback():
    primary, fallbacks = _pick_quality(
        {
            "flv_pull_url": {"FULL_HD1": "https://example.com/live.flv"},
            "hls_pull_url_map": {"FULL_HD1": "https://example.com/live.m3u8"},
        },
        "OD",
    )
    assert primary.endswith(".flv")
    assert fallbacks[0].endswith(".m3u8")


def test_douyin_detects_encrypted_cdn_url():
    assert _is_encrypted_douyin_stream(
        {
            "flv_pull_url": {
                "FULL_HD1": "https://pull-flv-l11-encryption.douyincdn.com/encryption/live.flv"
            }
        }
    )


def test_douyin_detects_encryption_flag_in_sdk_data():
    stream_data = json.dumps(
        {"data": {"origin": {"main": {"flv": "https://example.com/live.flv", "enableEncryption": True}}}}
    )
    assert _is_encrypted_douyin_stream(
        {"live_core_sdk_data": {"pull_data": {"stream_data": stream_data}}}
    )


def test_douyin_allows_normal_public_stream():
    assert not _is_encrypted_douyin_stream(
        {"flv_pull_url": {"FULL_HD1": "https://pull-flv.douyincdn.com/live.flv"}}
    )
