"""SeAT 公司成员 QQ 名单的抓取与缓存。

数据来源
--------
SeAT 的 paptracking 页面用 DataTables 服务端模式，成员列表由同一路径的
AJAX 请求返回 JSON（不是 HTML 表格）：

    GET /tools/paptracking?draw=1&start=0&length=200

单条记录包含 character / nickname / qq_id / pap_count 等字段；本模块按需求
只采集人员身份相关的部分（character、nickname、qq_id），PAP 数值不采集、
不保存。

抓取结果写入插件 KV（``seat:roster``），供指令与后台任务复用。
"""

from __future__ import annotations

import json
from datetime import datetime

import aiohttp
from astrbot.api import logger

from .seat_auth import BROWSER_UA, load_session

# 名单缓存键
ROSTER_KEY = "seat:roster"

# DataTables 单页条数；当前公司约百人，一次拉完避免翻页
PAGE_SIZE = 200

# 单次请求超时（秒）
REQUEST_TIMEOUT_SECONDS = 25

# DataTables 的 ajax 请求头（Accept 与 X-Requested-With 决定服务端返回 JSON）
AJAX_HEADERS = {
    "User-Agent": BROWSER_UA,
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "X-Requested-With": "XMLHttpRequest",
}


def _decode_text(value) -> str:
    """解码 SeAT 返回的文本字段。

    SeAT 的 nickname 会以 ``\\u5c0f\\u5361`` 这种 unicode 转义形式返回，
    前端是用 ``JSON.parse`` 还原的；这里用同样方式解一次，已经是普通文本
    的值保持原样。
    """
    if not value:
        return ""
    text = str(value)
    if "\\u" in text:
        try:
            return json.loads(f'"{text}"')
        except Exception:
            return text
    return text


def _normalize(items) -> list[dict]:
    """把接口返回的原始记录归一化成名单条目（只保留人员相关字段）。"""
    result: list[dict] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        qq = str(item.get("qq_id") or "").strip()
        if qq.lower() in ("", "0", "none", "null"):
            qq = ""
        result.append(
            {
                "character": _decode_text(item.get("character")).strip(),
                "nickname": _decode_text(item.get("nickname")).strip(),
                "qq_id": qq,
            }
        )
    return result


async def fetch_roster(plugin) -> dict:
    """带已保存的登录态拉取一次成员名单（不落库）。"""
    session = await load_session(plugin)
    if not session or not session.get("cookies"):
        raise RuntimeError(
            "尚未保存 SeAT 登录态，请先在插件页「SeAT 授权」视图里粘贴 Cookie"
        )

    cookies = session.get("cookies") or {}
    url = session.get("url") or ""
    if not url:
        raise RuntimeError("SeAT 登录态缺少目标地址，请重新保存 Cookie")

    params = {
        "draw": "1",
        "start": "0",
        "length": str(PAGE_SIZE),
        "search[value]": "",
    }

    timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
    async with aiohttp.ClientSession(timeout=timeout) as http:
        async with http.get(
            url,
            params=params,
            cookies=cookies,
            headers=AJAX_HEADERS,
            allow_redirects=False,
        ) as resp:
            if resp.status in (301, 302, 303, 307, 308):
                raise RuntimeError(
                    "SeAT 登录态已失效（请求被跳转到登录页），请重新粘贴 Cookie"
                )
            if resp.status != 200:
                raise RuntimeError(f"SeAT 返回 HTTP {resp.status}")
            payload = await resp.json(content_type=None)

    items = _normalize(payload.get("data") or [])
    total = payload.get("recordsTotal")
    total = int(total) if isinstance(total, (int, float)) else len(items)
    with_qq = sum(1 for item in items if item["qq_id"])

    return {
        "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total": total,
        "with_qq": with_qq,
        "items": items,
    }


async def refresh_roster(plugin) -> dict:
    """拉取名单并写入插件 KV。"""
    data = await fetch_roster(plugin)
    await plugin.put_kv_data(ROSTER_KEY, data)
    logger.info(
        "SeAT 名单已刷新：共 %d 人，%d 人有 QQ",
        data["total"],
        data["with_qq"],
    )
    return data


async def load_roster(plugin) -> dict | None:
    """读取最近一次缓存的名单。"""
    try:
        return await plugin.get_kv_data(ROSTER_KEY, None)
    except Exception as exc:
        logger.warning(f"读取 SeAT 名单失败：{exc}")
        return None
