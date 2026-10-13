from live_recorder.resolver import _pick_quality


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
