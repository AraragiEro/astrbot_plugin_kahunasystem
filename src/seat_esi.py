"""主角色信息解析：群名片里的「主角色名 / 公司缩写」都从这里来。

背景
----
paptracking 列出的 ``character`` 是「该成员在我们公司的角色」，不一定是
账号的主角色（主角色可能在别的公司）。群名片规则是

    {主角色公司缩写}-{昵称}/{主角色名}

所以需要两步补数据：

1. 打开该角色的 SeAT sheet 页，读取「主要角色」标签下的名字与链接 id；
2. 用主角色 id 走 ESI 查公司 ticker。

SeAT 页面需要登录态（复用已保存的 cookie）；ESI 是公开接口。
结果按公司角色名缓存 24 小时，ticker 变动很少。
"""

from __future__ import annotations

import asyncio
import re
import time

import aiohttp
from astrbot.api import logger

from .seat_auth import load_session

ESI_BASE = "https://esi.evetech.net/latest"
SEAT_BASE = "https://seat.winterco.org"

# 缓存键与有效期
ESI_CACHE_KEY = "seat:esi:characters"
CACHE_TTL_SECONDS = 24 * 3600

REQUEST_TIMEOUT_SECONDS = 40

# 角色间的小间隔，避免对 ESI / SeAT 造成压力
REQUEST_GAP_SECONDS = 0.2

# 并发度：sheet 页较大，限制同时请求的数量
SHEET_CONCURRENCY = 5

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

HEADERS = {"Accept": "application/json", "User-Agent": BROWSER_UA}

# 「主要角色」标签：<dt>主要角色</dt><dd><code><a href=".../sheet/{id}">名字</a>
MAIN_CHAR_RE = re.compile(
    r"主要角色</dt>\s*<dd>\s*<code>\s*<a[^>]*href=\"[^\"]*?/character/view/sheet/(\d+)\"[^>]*>\s*([^<]+?)\s*</a>",
    re.S,
)


async def _post_json(session: aiohttp.ClientSession, path: str, payload) -> dict:
    async with session.post(ESI_BASE + path, json=payload, headers=HEADERS) as resp:
        if resp.status != 200:
            raise RuntimeError(f"ESI {path} 返回 HTTP {resp.status}")
        return await resp.json(content_type=None)


async def _get_json(session: aiohttp.ClientSession, path: str) -> dict:
    async with session.get(ESI_BASE + path, headers=HEADERS) as resp:
        if resp.status != 200:
            raise RuntimeError(f"ESI {path} 返回 HTTP {resp.status}")
        return await resp.json(content_type=None)


async def _fetch_sheet(
    session: aiohttp.ClientSession, character_id: int, cookies: dict[str, str]
) -> str | None:
    """带 SeAT 登录态读取角色 sheet 页。"""
    url = f"{SEAT_BASE}/character/view/sheet/{character_id}"
    try:
        async with session.get(
            url,
            cookies=cookies,
            headers={
                "User-Agent": BROWSER_UA,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            },
            allow_redirects=False,
        ) as resp:
            if resp.status != 200:
                logger.warning(f"SeAT sheet 页返回 HTTP {resp.status}（角色 {character_id}）")
                return None
            return await resp.text(errors="ignore")
    except Exception as exc:
        logger.warning(f"读取 SeAT sheet 页失败（角色 {character_id}）：{exc}")
        return None


def parse_main_character(body: str) -> tuple[int | None, str | None]:
    """从 sheet 页解析「主要角色」的 (id, 名字)。"""
    if not body:
        return None, None
    match = MAIN_CHAR_RE.search(body)
    if not match:
        return None, None
    return int(match.group(1)), match.group(2).strip()


async def _resolve_one(
    session: aiohttp.ClientSession,
    sem: asyncio.Semaphore,
    character_id: int,
    cookies: dict[str, str],
) -> tuple[int | None, str | None]:
    async with sem:
        body = await _fetch_sheet(session, character_id, cookies)
        await asyncio.sleep(REQUEST_GAP_SECONDS)
        if not body:
            return None, None
        return parse_main_character(body)


async def _resolve_via_esi(
    plugin, names: list[str], cookies: dict[str, str]
) -> dict[str, dict]:
    """把公司角色名解析成 {name: {id, main_id, main_name, corp_id, ticker}}。"""
    resolved: dict[str, dict] = {}
    if not names:
        return resolved

    timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        # 1) 公司角色名 -> 角色 id
        id_map: dict[str, int] = {}
        for start in range(0, len(names), 500):
            batch = names[start : start + 500]
            try:
                data = await _post_json(session, "/universe/ids/", batch)
            except Exception as exc:
                logger.warning(f"ESI 角色名解析失败：{exc}")
                continue
            for item in data.get("characters") or []:
                name = item.get("name")
                cid = item.get("id")
                if name and cid:
                    id_map[name] = int(cid)

        # 2) 打开 sheet 页，读「主要角色」
        sem = asyncio.Semaphore(SHEET_CONCURRENCY)
        tasks = [
            _resolve_one(session, sem, cid, cookies) for cid in id_map.values()
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        info_by_name: dict[str, dict] = {}
        for (name, cid), result in zip(id_map.items(), results):
            if isinstance(result, Exception) or result is None:
                main_id, main_name = None, None
            else:
                main_id, main_name = result
            info_by_name[name] = {
                "id": cid,
                # 解析不到主角色时回退公司角色本身
                "main_id": main_id or cid,
                "main_name": main_name or name,
                "corp_id": None,
                "ticker": None,
            }

        # 3) 主角色 id -> 公司 id -> ticker（公司去重）
        corp_ids: set[int] = set()
        for info in info_by_name.values():
            try:
                char = await _get_json(session, f"/characters/{info['main_id']}/")
                info["corp_id"] = char.get("corporation_id")
                if info["corp_id"]:
                    corp_ids.add(info["corp_id"])
            except Exception as exc:
                logger.warning(f"ESI 查询主角色 {info['main_name']} 失败：{exc}")
            await asyncio.sleep(REQUEST_GAP_SECONDS)

        tickers: dict[int, str] = {}
        for corp_id in corp_ids:
            try:
                corp = await _get_json(session, f"/corporations/{corp_id}/")
                if corp.get("ticker"):
                    tickers[corp_id] = str(corp["ticker"])
            except Exception as exc:
                logger.warning(f"ESI 查询公司 {corp_id} 失败：{exc}")
            await asyncio.sleep(REQUEST_GAP_SECONDS)

        for name, info in info_by_name.items():
            info["ticker"] = tickers.get(info.get("corp_id"))
            resolved[name] = info

    return resolved


async def resolve_main_characters(
    plugin, names: list[str], force: bool = False
) -> dict[str, dict]:
    """返回 {公司角色名: {id, main_id, main_name, corp_id, ticker}}，带 24 小时缓存。"""
    names = [str(name).strip() for name in (names or []) if str(name).strip()]
    if not names:
        return {}

    cache = await plugin.get_kv_data(ESI_CACHE_KEY, None) or {}
    cached: dict[str, dict] = dict(cache.get("characters") or {})
    now = time.time()
    age = now - float(cache.get("updated_at") or 0)

    if not force and age < CACHE_TTL_SECONDS:
        missing = [name for name in names if name not in cached]
        if not missing:
            return cached
        todo = missing
    else:
        todo = names

    session_data = await load_session(plugin)
    if not session_data or not session_data.get("cookies"):
        raise RuntimeError("尚未保存 SeAT 登录态，无法读取角色页")

    resolved = await _resolve_via_esi(plugin, todo, session_data["cookies"])
    if resolved:
        cached.update(resolved)
        await plugin.put_kv_data(
            ESI_CACHE_KEY,
            {"updated_at": now, "characters": cached},
        )
    return cached
