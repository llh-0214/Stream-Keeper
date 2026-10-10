from __future__ import annotations

from urllib.parse import urlparse

from .models import Platform


class UnsupportedPlatform(ValueError):
    pass


ALLOWED_HOSTS = {
    Platform.DOUYIN: {"live.douyin.com", "v.douyin.com", "www.douyin.com", "douyin.com"},
    Platform.XIAOHONGSHU: {
        "www.xiaohongshu.com",
        "xiaohongshu.com",
        "xhslink.com",
        "www.xhslink.com",
        "xhs.cn",
        "www.xhs.cn",
    },
}


def normalize_and_detect(raw_url: str) -> tuple[str, Platform]:
    url = raw_url.strip()
    if not url:
        raise UnsupportedPlatform("请输入直播间链接")
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    for platform, hosts in ALLOWED_HOSTS.items():
        if host in hosts:
            return url, platform
    raise UnsupportedPlatform("目前只支持抖音和小红书直播间链接")

