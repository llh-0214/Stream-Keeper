from __future__ import annotations

import json
import re
import urllib.parse
from typing import Any

import httpx

from .models import Platform, StreamInfo
from .vendor_ab_sign import ab_sign

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
QUALITY_INDEX = {"OD": 0, "UHD": 1, "HD": 2, "SD": 3, "LD": 4}
BILIBILI_QUALITY = {"OD": 10000, "UHD": 10000, "HD": 400, "SD": 250, "LD": 80}


class ResolveError(RuntimeError):
    pass


class ProtectedStreamError(ResolveError):
    pass


def _is_encrypted_douyin_stream(stream_url: dict[str, Any]) -> bool:
    """Return whether Douyin only exposed a membership/encrypted media stream."""
    urls: list[str] = []
    for key in ("flv_pull_url", "hls_pull_url_map"):
        urls.extend(str(value) for value in (stream_url.get(key) or {}).values())

    pull_data = stream_url.get("live_core_sdk_data", {}).get("pull_data", {})
    raw = pull_data.get("stream_data")
    if raw:
        try:
            data = json.loads(raw).get("data", {})
        except (json.JSONDecodeError, TypeError):
            data = {}
        for item in data.values():
            main = item.get("main", {})
            if main.get("enableEncryption") is True:
                return True
            urls.extend(str(main[key]) for key in ("flv", "hls") if main.get(key))

    parsed = [urllib.parse.urlparse(url) for url in urls]
    return any(
        (item.hostname and "-encryption." in item.hostname.lower())
        or "/encryption/" in item.path.lower()
        for item in parsed
    )


def _clean(value: Any, default: str = "") -> str:
    return str(value) if value is not None else default


def _pick_quality(stream_url: dict[str, Any], quality: str) -> tuple[str, tuple[str, ...]]:
    hls = list((stream_url.get("hls_pull_url_map") or {}).values())
    flv = list((stream_url.get("flv_pull_url") or {}).values())
    if not hls and not flv:
        pull_data = stream_url.get("live_core_sdk_data", {}).get("pull_data", {})
        raw = pull_data.get("stream_data")
        if raw:
            data = json.loads(raw).get("data", {})
            for item in data.values():
                main = item.get("main", {})
                if main.get("flv"):
                    flv.append(main["flv"])
                if main.get("hls"):
                    hls.append(main["hls"])
    if not flv and not hls:
        raise ResolveError("直播已开播，但没有找到可录制的视频流")

    # Douyin HLS playlists can remain reachable while their media segments have
    # already expired. FLV is normally a single long-lived response and is more
    # reliable for unattended recording, so use it first and retain HLS as an
    # immediate fallback.
    preferred = flv or hls
    alternate = hls if flv else []
    requested_index = QUALITY_INDEX.get(quality, 0)
    primary = preferred[min(requested_index, len(preferred) - 1)]
    fallbacks: list[str] = []
    if alternate:
        fallbacks.append(alternate[min(requested_index, len(alternate) - 1)])
    fallbacks.extend(url for url in preferred if url != primary)
    fallbacks.extend(url for url in alternate if url not in fallbacks)
    return primary, tuple(url for url in fallbacks if url != primary)


async def _fetch_douyin_room(url: str, cookie: str) -> dict[str, Any]:
    headers = {"user-agent": USER_AGENT, "referer": "https://live.douyin.com/"}
    if cookie.strip():
        headers["cookie"] = cookie.strip()
    timeout = httpx.Timeout(20.0)
    async with httpx.AsyncClient(headers=headers, timeout=timeout, follow_redirects=True) as client:
        parsed = urllib.parse.urlparse(url)
        if parsed.hostname == "live.douyin.com" and parsed.path.strip("/"):
            web_rid = parsed.path.strip("/").split("/")[0]
            # Douyin issues an anonymous ttwid cookie on the room page. The status API
            # returns a challenge page instead of JSON when this handshake is skipped.
            await client.get(url)
            params = {
                "aid": "6383", "app_name": "douyin_web", "live_id": "1",
                "device_platform": "web", "language": "zh-CN",
                "browser_language": "zh-CN", "browser_platform": "Win32",
                "browser_name": "Chrome", "browser_version": "131.0.0.0",
                "web_rid": web_rid, "msToken": "",
            }
            query = urllib.parse.urlencode(params)
            endpoint = f"https://live.douyin.com/webcast/room/web/enter/?{query}&a_bogus={urllib.parse.quote(ab_sign(query, USER_AGENT))}"
            response = await client.get(endpoint)
            response.raise_for_status()
            payload = response.json().get("data", {})
            rooms = payload.get("data") or []
            if not rooms:
                return {"status": 4, "anchor_name": web_rid}
            room = rooms[0]
            room["anchor_name"] = (payload.get("user") or {}).get("nickname", web_rid)
            return room

        response = await client.get(url)
        final_url = str(response.url)
        match = re.search(r"/reflow/([^/?]+).*?[?&]sec_user_id=([^&]+)", final_url)
        if not match:
            match = re.search(r"room_id[=:]%?22?([^&\"']+).*?sec_user_id[=:]%?22?([^&\"']+)", response.text)
        if not match:
            raise ResolveError("无法识别这个抖音分享链接，请改用 live.douyin.com 的直播间链接")
        room_id, sec_user_id = match.group(1), urllib.parse.unquote(match.group(2))
        params = {
            "verifyFp": "verify_stream_keeper", "type_id": "0", "live_id": "1",
            "room_id": room_id, "sec_user_id": sec_user_id,
            "version_code": "99.99.99", "app_id": "1128",
        }
        query = urllib.parse.urlencode(params)
        endpoint = f"https://webcast.amemv.com/webcast/room/reflow/info/?{query}&a_bogus={urllib.parse.quote(ab_sign(query, USER_AGENT))}"
        payload = (await client.get(endpoint)).json().get("data", {})
        room = payload.get("room") or {"status": 4}
        room["anchor_name"] = (room.get("owner") or {}).get("nickname", "抖音主播")
        return room


async def resolve_douyin(url: str, quality: str, cookie: str = "") -> StreamInfo:
    room = await _fetch_douyin_room(url, cookie)
    anchor = _clean(room.get("anchor_name"), "抖音主播")
    if room.get("status", 4) != 2:
        return StreamInfo(Platform.DOUYIN, url, anchor, "", False)
    stream_url = room.get("stream_url") or {}
    if _is_encrypted_douyin_stream(stream_url):
        raise ProtectedStreamError(
            "检测到会员加密直播，平台未提供普通可录制视频流；已停止录制，不会生成无效文件"
        )
    primary_url, fallback_urls = _pick_quality(stream_url, quality)
    cookie_header = f"Cookie: {cookie.strip()}\r\n" if cookie.strip() else ""
    return StreamInfo(
        Platform.DOUYIN, url, anchor, _clean(room.get("title"), "直播"), True,
        primary_url,
        f"Referer: https://live.douyin.com/\r\nUser-Agent: {USER_AGENT}\r\n{cookie_header}",
        fallback_urls,
    )


def _query_value(url: str, key: str) -> str:
    return urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get(key, [""])[0]


async def resolve_xiaohongshu(url: str, cookie: str = "") -> StreamInfo:
    headers = {
        "User-Agent": "ios/7.830 (ios 17.0; iPhone 15)",
        "xy-common-params": "platform=iOS&sid=session.streamkeeper",
        "referer": "https://app.xhs.cn/",
    }
    if cookie.strip():
        headers["Cookie"] = cookie.strip()
    async with httpx.AsyncClient(headers=headers, timeout=20, follow_redirects=True, verify=False) as client:
        response = await client.get(url)
        final_url = str(response.url)
        html = response.text
        match = re.search(r"<script>window\.__INITIAL_STATE__=(.*?)</script>", html, re.DOTALL)
        if match:
            state = json.loads(match.group(1).replace("undefined", "null"))
            live = state.get("liveStream") or {}
            if live.get("liveStatus") == "success":
                info = live.get("roomData", {}).get("roomInfo", {})
                title = _clean(info.get("roomTitle"), "直播")
                if "回放" not in title:
                    deeplink = _clean(info.get("deeplink"))
                    flv_url = urllib.parse.unquote(_query_value(deeplink, "flvUrl"))
                    if flv_url:
                        anchor = urllib.parse.unquote(_query_value(deeplink, "host_nickname")) or "小红书主播"
                        return StreamInfo(
                            Platform.XIAOHONGSHU, final_url, anchor, title, True, flv_url,
                            "Referer: https://app.xhs.cn/\r\n",
                        )
        title_match = re.search(r"<title>@?(.*?)\s*(?:的个人主页)?</title>", html, re.DOTALL)
        anchor = title_match.group(1).strip() if title_match else "小红书主播"
        return StreamInfo(Platform.XIAOHONGSHU, final_url, anchor, "", False)


async def resolve_bilibili(url: str, quality: str) -> StreamInfo:
    headers = {"User-Agent": USER_AGENT, "Referer": "https://live.bilibili.com/"}
    async with httpx.AsyncClient(headers=headers, timeout=20, follow_redirects=True) as client:
        page = await client.get(url)
        page.raise_for_status()
        final_url = str(page.url)
        match = re.search(r"live\.bilibili\.com/(\d+)", final_url)
        if not match:
            raise ResolveError("无法识别哔哩哔哩直播间，请使用 live.bilibili.com 的房间链接")
        short_id = match.group(1)
        init_response = await client.get(
            "https://api.live.bilibili.com/room/v1/Room/room_init", params={"id": short_id}
        )
        init_response.raise_for_status()
        init_data = init_response.json().get("data") or {}
        room_id = str(init_data.get("room_id") or short_id)
        info_response = await client.get(
            "https://api.live.bilibili.com/room/v1/Room/get_info",
            params={"room_id": room_id},
        )
        info_response.raise_for_status()
        info = info_response.json().get("data") or {}
        anchor_response = await client.get(
            "https://api.live.bilibili.com/live_user/v1/UserInfo/get_anchor_in_room",
            params={"roomid": room_id},
        )
        anchor_response.raise_for_status()
        anchor_info = ((anchor_response.json().get("data") or {}).get("info") or {})
        anchor = _clean(anchor_info.get("uname"), f"哔哩哔哩主播{room_id}")
        title = _clean(info.get("title"), "直播")
        if int(init_data.get("live_status") or info.get("live_status") or 0) != 1:
            return StreamInfo(Platform.BILIBILI, final_url, anchor, title, False)

        play_response = await client.get(
            "https://api.live.bilibili.com/xlive/web-room/v2/index/getRoomPlayInfo",
            params={
                "room_id": room_id,
                "protocol": "0,1",
                "format": "0,1,2",
                "codec": "0,1",
                "qn": BILIBILI_QUALITY.get(quality, 10000),
                "platform": "web",
                "ptype": "8",
            },
        )
        play_response.raise_for_status()
        playurl = ((play_response.json().get("data") or {}).get("playurl_info") or {}).get("playurl") or {}
        candidates: list[tuple[int, str]] = []
        for stream in playurl.get("stream") or []:
            protocol = stream.get("protocol_name", "")
            for format_info in stream.get("format") or []:
                format_name = format_info.get("format_name", "")
                for codec in format_info.get("codec") or []:
                    base_url = codec.get("base_url") or ""
                    for url_info in codec.get("url_info") or []:
                        full_url = (url_info.get("host") or "") + base_url + (url_info.get("extra") or "")
                        if full_url:
                            priority = 0 if protocol == "http_hls" and format_name == "fmp4" else 1
                            candidates.append((priority, full_url))
        if not candidates:
            raise ResolveError("直播已开播，但没有找到可录制的视频流")
        candidates.sort(key=lambda item: item[0])
        return StreamInfo(
            Platform.BILIBILI,
            final_url,
            anchor,
            title,
            True,
            candidates[0][1],
            f"Referer: https://live.bilibili.com/{room_id}\r\nUser-Agent: {USER_AGENT}\r\n",
        )


async def resolve_generic(url: str) -> StreamInfo:
    parsed = urllib.parse.urlparse(url)
    name = urllib.parse.unquote(parsed.path.rsplit("/", 1)[-1]).split("?")[0]
    anchor = name.rsplit(".", 1)[0] if name else "通用直播流"
    return StreamInfo(Platform.GENERIC, url, anchor or "通用直播流", "直播", True, url)


async def resolve(platform: Platform, url: str, quality: str, cookie: str = "") -> StreamInfo:
    try:
        if platform is Platform.DOUYIN:
            return await resolve_douyin(url, quality, cookie)
        if platform is Platform.XIAOHONGSHU:
            return await resolve_xiaohongshu(url, cookie)
        if platform is Platform.BILIBILI:
            return await resolve_bilibili(url, quality)
        return await resolve_generic(url)
    except ResolveError:
        raise
    except Exception as exc:
        raise ResolveError(f"获取直播状态失败：{exc}") from exc
