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


class ResolveError(RuntimeError):
    pass


def _clean(value: Any, default: str = "") -> str:
    return str(value) if value is not None else default


def _pick_quality(stream_url: dict[str, Any], quality: str) -> str:
    hls = list((stream_url.get("hls_pull_url_map") or {}).values())
    flv = list((stream_url.get("flv_pull_url") or {}).values())
    candidates = hls or flv
    if not candidates:
        pull_data = stream_url.get("live_core_sdk_data", {}).get("pull_data", {})
        raw = pull_data.get("stream_data")
        if raw:
            data = json.loads(raw).get("data", {})
            for item in data.values():
                main = item.get("main", {})
                if main.get("hls"):
                    candidates.append(main["hls"])
                elif main.get("flv"):
                    candidates.append(main["flv"])
    if not candidates:
        raise ResolveError("直播已开播，但没有找到可录制的视频流")
    index = min(QUALITY_INDEX.get(quality, 0), len(candidates) - 1)
    return candidates[index]


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
    return StreamInfo(
        Platform.DOUYIN, url, anchor, _clean(room.get("title"), "直播"), True,
        _pick_quality(stream_url, quality),
        f"Referer: https://live.douyin.com/\r\nUser-Agent: {USER_AGENT}\r\n",
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


async def resolve(platform: Platform, url: str, quality: str, cookie: str = "") -> StreamInfo:
    try:
        if platform is Platform.DOUYIN:
            return await resolve_douyin(url, quality, cookie)
        return await resolve_xiaohongshu(url, cookie)
    except ResolveError:
        raise
    except Exception as exc:
        raise ResolveError(f"获取直播状态失败：{exc}") from exc
