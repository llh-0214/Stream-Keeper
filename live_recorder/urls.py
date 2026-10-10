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
    Platform.BILIBILI: {"live.bilibili.com", "b23.tv", "www.bilibili.com", "bilibili.com"},
}


def normalize_and_detect(raw_url: str) -> tuple[str, Platform]:
    url = raw_url.strip()
    if not url:
        raise UnsupportedPlatform("请输入直播间链接")
    if not url.startswith(("http://", "https://", "rtmp://", "rtmps://")):
        url = "https://" + url
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if not host or any(character.isspace() for character in host):
        raise UnsupportedPlatform("请输入有效的直播间或直播流地址")
    for platform, hosts in ALLOWED_HOSTS.items():
        if host in hosts:
            return url, platform
    if parsed.scheme in {"http", "https", "rtmp", "rtmps"} and host:
        return url, Platform.GENERIC
    raise UnsupportedPlatform("请输入抖音、小红书、哔哩哔哩直播间链接，或 HTTP/RTMP 直播流地址")
