import pytest

from live_recorder.models import Platform
from live_recorder.urls import UnsupportedPlatform, normalize_and_detect


@pytest.mark.parametrize(
    ("url", "platform"),
    [
        ("https://live.douyin.com/123", Platform.DOUYIN),
        ("v.douyin.com/abc", Platform.DOUYIN),
        ("https://www.xiaohongshu.com/user/profile/abc", Platform.XIAOHONGSHU),
        ("https://xhslink.com/a1b2", Platform.XIAOHONGSHU),
    ],
)
def test_supported_urls(url, platform):
    normalized, detected = normalize_and_detect(url)
    assert normalized.startswith("https://")
    assert detected is platform


def test_rejects_other_platform():
    with pytest.raises(UnsupportedPlatform):
        normalize_and_detect("https://example.com/live")

