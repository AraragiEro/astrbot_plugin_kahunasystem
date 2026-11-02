"""SeAT 登录态（Cookie）的校验与保存。

背景
----
联盟 SeAT 是 Laravel 应用，登录后依靠两个 httpOnly Cookie 维持会话：

- ``seat_session``：Laravel session（默认 120 分钟空闲过期）；
- ``remember_web_*``：Laravel 的 remember cookie（长效，session 过期后自动重建）。

这两个 Cookie 都是 httpOnly，浏览器里的插件视图页面无法通过 JS 读取，因此
由用户在浏览器中复制后粘贴进来，插件后端用 aiohttp 请求一次 paptracking
页面来校验：

- 200 且不是登录页  -> 有效；
- 302 到 /auth/login 或 /auth/eve -> 失效；
- 403/429/503 且带 Cloudflare 特征 -> 被拦截。

校验通过后把 Cookie 存进插件 KV（``seat:session``），供后续抓取复用。
**不存配置项**：配置项会明文落在 ``data/config/*.json``，KV 在
``data_v4.db`` 里更收敛，且插件更新不会丢。
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime

import aiohttp
from astrbot.api import logger
from astrbot.api.web import json_response, request

# 与 @register 装饰器中的插件名保持一致（register_web_api 的路由前缀）
PLUGIN_NAME = "astrbot_plugin_kahunasystem"

# 插件 KV 中保存登录态的键
KV_KEY = "seat:session"

DEFAULT_BASE_URL = "https://seat.winterco.org"
DEFAULT_TARGET_PATH = "/tools/paptracking"

# 单次请求超时（秒）
REQUEST_TIMEOUT_SECONDS = 20

# 只读页面头部，页面通常几十 KB，这里给足余量又避免异常大响应打爆内存
MAX_BODY_BYTES = 300_000

# 站点前有 Cloudflare，默认的 python UA 容易触发挑战，这里伪装成普通浏览器
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# SeAT 登录页的确定性特征（正常登录态下不会出现）
LOGIN_MARKERS = (
    "请以 EVE Online SSO 方式登入",
    "Welcome, please login using EVE Online SSO",
    "SeAT | Sign In",
    "SeAT | 登入",
)

# Cloudflare 挑战页/拦截页特征
CF_MARKERS = (
    "Just a moment",
    "cf-browser-verification",
    "Attention Required",
    "Checking your browser",
)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def parse_cookie_string(raw: str) -> dict[str, str]:
    """把浏览器复制的 Cookie 字符串解析成 dict。

    容错常见粘贴格式：允许 ``Cookie:`` 前缀、换行/分号混合分隔、多余空格、
    值两侧的引号；只保留合法 Cookie 名，避免把无关文本当成 Cookie。
    """
    text = (raw or "").strip()
    if not text:
        return {}
    if text.lower().startswith("cookie:"):
        text = text.split(":", 1)[1]

    cookies: dict[str, str] = {}
    for part in re.split(r"[;\r\n]+", text):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, _, value = part.partition("=")
        name = name.strip()
        value = value.strip().strip('"').strip("'")
        # Cookie 名只允许常见字符，过滤掉粘贴时混入的说明文字
        if name and re.fullmatch(r"[A-Za-z0-9_\-\.]+", name):
            cookies[name] = value
    return cookies


def _merge_set_cookies(base: dict[str, str], morsels) -> dict[str, str]:
    """把响应 Set-Cookie 合并进原 Cookie。

    Laravel 在 session 过期、靠 remember cookie 重建时会轮换 session id，
    不合并的话，刚校验通过的 Cookie 在下一次请求时就作废了。
    同时处理 Max-Age=0 / 过期时间，把被服务端删除的 Cookie 剔除。
    """
    merged = dict(base)
    for morsel in morsels:
        key = getattr(morsel, "key", "") or ""
        if not key:
            continue
        max_age_raw = morsel.get("max-age", "")
        expires_raw = morsel.get("expires", "")
        # 注意：max-age=0 是 int，不能写成 `x or ""`，否则 0 会被吞成空串
        max_age = "" if max_age_raw is None else str(max_age_raw).strip()
        expires = "" if expires_raw is None else str(expires_raw)
        if max_age == "0" or "1970" in expires:
            merged.pop(key, None)
        else:
            merged[key] = morsel.value
    return merged


def _looks_like_login_page(body: str) -> bool:
    if not body:
        return False
    lowered = body.lower()
    return any(marker.lower() in lowered for marker in LOGIN_MARKERS)


def _looks_like_cloudflare(status: int, body: str, headers: dict) -> bool:
    if headers.get("cf-mitigated"):
        return True
    lowered = (body or "").lower()
    if any(marker.lower() in lowered for marker in CF_MARKERS):
        return True
    return status in (403, 429, 503) and "cloudflare" in lowered


def _is_login_redirect(location: str) -> bool:
    lowered = (location or "").lower()
    return "/auth/login" in lowered or "/auth/eve" in lowered


# SeAT 的 session cookie 名取决于 APP_NAME 配置：
# - APP_NAME=SeAT 时会变成 seat_session；
# - 未配置（或沿用 Laravel 默认）时是 laravel_session。
# 实测 winterco 站点用的是 laravel_session，这里两种都认。
SESSION_COOKIE_ALIASES = ("laravel_session", "seat_session")


def _apply_session_alias(cookies: dict[str, str]) -> dict[str, str]:
    """把已存在的 session cookie 名补齐成两种别名，兼容不同 SeAT 配置。

    只做「缺哪个补哪个」，不覆盖已有值；两种都不存在时原样返回。
    """
    for name in SESSION_COOKIE_ALIASES:
        if name in cookies:
            for other in SESSION_COOKIE_ALIASES:
                if other not in cookies:
                    cookies[other] = cookies[name]
    return cookies


async def probe(cookies: dict[str, str], url: str) -> dict:
    """带 Cookie 请求一次目标页面，返回校验结论。

    返回的 dict 至少包含 ``status``（ok/expired/blocked/error）与 ``message``；
    成功时额外带 ``cookies``（合并了响应 Set-Cookie 后的完整集合）。
    """
    headers = {
        "User-Agent": BROWSER_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }

    try:
        timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(
                url,
                cookies=cookies,
                headers=headers,
                allow_redirects=False,
            ) as resp:
                status = resp.status
                location = resp.headers.get("Location", "") or ""
                try:
                    raw = await resp.content.read(MAX_BODY_BYTES)
                except Exception:
                    raw = b""
                encoding = resp.charset or "utf-8"
                body = raw.decode(encoding, errors="ignore")
                morsels = list(getattr(resp, "cookies", {}).values())
                resp_headers = dict(resp.headers)
    except asyncio.TimeoutError:
        return {"status": "error", "message": "连接 SeAT 超时，请检查 NAS 的出网网络"}
    except aiohttp.ClientError as exc:
        return {
            "status": "error",
            "message": f"连接 SeAT 失败（{type(exc).__name__}）",
        }

    # 1) 被重定向到登录页：Cookie 已失效
    if status in (301, 302, 303, 307, 308) and _is_login_redirect(location):
        return {
            "status": "expired",
            "message": "Cookie 已失效（请求被跳转到登录页），请重新登录后粘贴",
        }

    # 2) Cloudflare 拦截：与 Cookie 无关，换个时间/网络再试
    if _looks_like_cloudflare(status, body, resp_headers):
        return {
            "status": "blocked",
            "message": "请求被 Cloudflare 拦截，请稍后重试或检查 NAS 网络",
        }

    # 3) 200 但内容是登录页：同样视为失效
    if status == 200 and _looks_like_login_page(body):
        return {
            "status": "expired",
            "message": "Cookie 已失效（返回的是登录页），请重新登录后粘贴",
        }

    # 4) 其它错误码
    if status >= 400:
        return {"status": "error", "message": f"SeAT 返回 HTTP {status}"}

    # 5) 2xx：有效，合并响应里轮换后的 Cookie
    return {
        "status": "ok",
        "message": "验证通过",
        "cookies": _merge_set_cookies(cookies, morsels),
        "http_status": status,
    }


def _cfg(plugin, key: str, default: str) -> str:
    """读取插件配置项，缺失或为空时回退默认值。"""
    config = getattr(plugin, "config", None)
    if config is None:
        return default
    try:
        value = config.get(key)
    except Exception:
        return default
    if value is None:
        return default
    text = str(value).strip()
    return text or default


async def handle_validate_request(plugin):
    """视图页面「验证并保存」按钮的后端处理函数。"""
    payload = await request.json(default={})
    raw = str(payload.get("cookie") or "").strip()
    if not raw:
        return json_response({"status": "error", "message": "请先粘贴 Cookie"})

    cookies = _apply_session_alias(parse_cookie_string(raw))
    if not cookies:
        return json_response(
            {
                "status": "error",
                "message": "未能解析出 Cookie，格式应形如 seat_session=...; remember_web_...=...",
            }
        )

    base_url = _cfg(plugin, "seat_base_url", DEFAULT_BASE_URL)
    target_path = _cfg(plugin, "seat_paptracking_path", DEFAULT_TARGET_PATH)
    url = f"{base_url.rstrip('/')}/{target_path.lstrip('/')}"

    result = await probe(cookies, url)
    # 只记状态与数量，绝不把 Cookie 值写进日志
    logger.info(
        "SeAT Cookie 校验：%s（%d 个 cookie）",
        result.get("status"),
        len(cookies),
    )

    if result.get("status") == "ok":
        merged = result.get("cookies") or cookies
        now = _now()
        await plugin.put_kv_data(
            KV_KEY,
            {
                "cookies": merged,
                "url": url,
                "saved_at": now,
                "last_valid_at": now,
            },
        )
        return json_response(
            {
                "status": "ok",
                "message": f"验证通过，已保存 {len(merged)} 个 Cookie",
            }
        )

    return json_response(
        {
            "status": result.get("status", "error"),
            "message": result.get("message", "校验失败"),
        }
    )


async def load_session(plugin) -> dict | None:
    """读取已保存的 SeAT 登录态（供后续 PAP 抓取复用）。"""
    try:
        return await plugin.get_kv_data(KV_KEY, None)
    except Exception as exc:
        logger.warning(f"读取 SeAT 登录态失败：{exc}")
        return None
