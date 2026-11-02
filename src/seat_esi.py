"""通过 ESI 查询角色所属公司的 ticker，用于生成群名片前缀。

背景
----
群名片规则是 ``{主角色公司缩写}-{昵称}/{主角色名}``，而主角色所在公司
不一定是我们公司。SeAT 的 paptracking 接口只给角色名，没有公司信息，
因此需要用公开的 ESI 补一步：

    character name  ->  character_id   (POST /universe/ids/)
    character_id    ->  corporation_id (GET /characters/{id}/)
    corporation_id  ->  ticker         (GET /corporations/{id}/)

结果按角色名缓存 24 小时（ticker 变动很少），避免每小时重复请求。
"""

from __future__ import annotations

import asyncio
import time

import aiohttp
from astrbot.api import logger

ESI_BASE = "https://esi.evetech.net/latest"

# 缓存键与有效期
ESI_CACHE_KEY = "seat:esi:characters"
CACHE_TTL_SECONDS = 24 * 3600

REQUEST_TIMEOUT_SECONDS = 20

# 角色间的小间隔，避免对 ESI 造成压力
REQUEST_GAP_SECONDS = 0.05

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

HEADERS = {"Accept": "application/json", "User-Agent": BROWSER_UA}


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


async def _resolve_via_esi(names: list[str]) -> dict[str, dict]:
    """把角色名解析成 {name: {id, corp_id, ticker}}。"""
    resolved: dict[str, dict] = {}
    if not names:
        return resolved

    timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        # 1) 角色名 -> 角色 id（接口单次最多 1000 个，这里分批）
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

        # 2) 角色 id -> 公司 id
        corp_by_name: dict[str, dict] = {}
        for name, cid in id_map.items():
            try:
                char = await _get_json(session, f"/characters/{cid}/")
                corp_by_name[name] = {
                    "id": cid,
                    "corp_id": char.get("corporation_id"),
                    "ticker": None,
                }
            except Exception as exc:
                logger.warning(f"ESI 查询角色 {name} 失败：{exc}")
                corp_by_name[name] = {"id": cid, "corp_id": None, "ticker": None}
            await asyncio.sleep(REQUEST_GAP_SECONDS)

        # 3) 公司 id -> ticker（去重后查询）
        corp_ids = {
            info["corp_id"] for info in corp_by_name.values() if info.get("corp_id")
        }
        tickers: dict[int, str] = {}
        for corp_id in corp_ids:
            try:
                corp = await _get_json(session, f"/corporations/{corp_id}/")
                ticker = corp.get("ticker")
                if ticker:
                    tickers[corp_id] = str(ticker)
            except Exception as exc:
                logger.warning(f"ESI 查询公司 {corp_id} 失败：{exc}")
            await asyncio.sleep(REQUEST_GAP_SECONDS)

        for name, info in corp_by_name.items():
            info["ticker"] = tickers.get(info.get("corp_id"))
            resolved[name] = info

    return resolved


async def resolve_tickers(
    plugin, names: list[str], force: bool = False
) -> dict[str, dict]:
    """返回 {角色名: {id, corp_id, ticker}}，带 24 小时缓存。"""
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

    resolved = await _resolve_via_esi(todo)
    if resolved:
        cached.update(resolved)
        await plugin.put_kv_data(
            ESI_CACHE_KEY,
            {"updated_at": now, "characters": cached},
        )
    return cached
