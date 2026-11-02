# -*- coding: utf-8 -*-
"""SeAT PAP 月度采集。

复用 ``src/seat_auth.py`` 保存的登录态。

⚠️ 必须显式传 year/month：不传参数时 SeAT 返回「上个月」，
口径会随运行日期漂移，不能用于结算（已实测，见 plan/member_bank_design.md）。
"""
from __future__ import annotations

from datetime import datetime, timezone

import aiohttp
from astrbot.api import logger

from ..seat_auth import BROWSER_UA, load_session
from .parsing import normalize_pap_rows

# DataTables 单页条数；当前公司约百人，一次即可拉完，留翻页兜底
PAGE_SIZE = 500
MAX_PAGES = 10
REQUEST_TIMEOUT_SECONDS = 45

AJAX_HEADERS = {
    "User-Agent": BROWSER_UA,
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "X-Requested-With": "XMLHttpRequest",
}


class PapFetchError(RuntimeError):
    """采集失败（登录态失效 / 网络 / 响应异常）。"""


def build_period(year: int, month: int) -> str:
    year, month = int(year), int(month)
    if not (2000 <= year <= 2100):
        raise ValueError(f"年份不合法：{year}")
    if not (1 <= month <= 12):
        raise ValueError(f"月份不合法：{month}")
    return f"{year:04d}-{month:02d}"


def previous_period(period: str) -> str:
    """``2026-01`` 的上一个月是 ``2025-12``。"""
    year, month = (int(x) for x in str(period).split("-"))
    if month == 1:
        return f"{year - 1:04d}-12"
    return f"{year:04d}-{month - 1:02d}"


def last_completed_month(now: datetime | None = None) -> str:
    """当前时间下「上一个已结束的月份」，形如 ``2026-10``。"""
    now = now or datetime.now(timezone.utc)
    if now.month == 1:
        return f"{now.year - 1:04d}-12"
    return f"{now.year:04d}-{now.month - 1:02d}"


async def fetch_pap_month(plugin, year: int, month: int) -> dict:
    """拉取指定月份的成员 PAP。

    返回 ``{"period", "fetched_at", "records_total", "rows": [...]}``。
    登录态失效抛 ``PapFetchError``，由调用方提示重新粘贴 Cookie。
    """
    period = build_period(year, month)
    session = await load_session(plugin)
    if not session or not session.get("cookies"):
        raise PapFetchError(
            "尚未保存 SeAT 登录态，请先在插件页「SeAT 授权」视图里粘贴 Cookie"
        )
    cookies = session.get("cookies") or {}
    url = session.get("url") or ""
    if not url:
        raise PapFetchError("SeAT 登录态缺少目标地址，请重新保存 Cookie")

    all_rows: list[dict] = []
    records_total = 0
    timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)

    async with aiohttp.ClientSession(timeout=timeout) as http:
        for page in range(MAX_PAGES):
            params = {
                "draw": str(page + 1),
                "start": str(page * PAGE_SIZE),
                "length": str(PAGE_SIZE),
                "search[value]": "",
                "year": str(int(year)),
                "month": str(int(month)),
            }
            try:
                async with http.get(
                    url, params=params, cookies=cookies,
                    headers=AJAX_HEADERS, allow_redirects=False,
                ) as resp:
                    if resp.status in (301, 302, 303, 307, 308):
                        raise PapFetchError(
                            "SeAT 登录态已失效（被重定向到登录页），请重新粘贴 Cookie"
                        )
                    if resp.status != 200:
                        raise PapFetchError(f"SeAT 返回 HTTP {resp.status}")
                    payload = await resp.json(content_type=None)
            except aiohttp.ClientError as exc:
                raise PapFetchError(f"连接 SeAT 失败（{type(exc).__name__}）") from exc
            except ValueError as exc:
                raise PapFetchError("SeAT 返回的不是 JSON，可能被 Cloudflare 拦截") from exc

            batch = normalize_pap_rows(payload)
            records_total = int(payload.get("recordsTotal") or records_total or 0)
            all_rows.extend(batch)
            if len(batch) < PAGE_SIZE or len(all_rows) >= records_total:
                break

    logger.info(
        "PAP 采集完成：%s 共 %d 行（recordsTotal=%d）",
        period, len(all_rows), records_total,
    )
    return {
        "period": period,
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "records_total": records_total,
        "rows": all_rows,
    }
