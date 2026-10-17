import json

import aiohttp
from astrbot.api import logger

# 服务间认证密钥：由 main.py 在插件初始化时通过 set_service_api_key() 注入。
# kahunabot 无法对 QQ 机器人做交互式登录，因此改用共享服务密钥（请求头 X-Internal-API-Key）。
_SERVICE_API_KEY = ""

# 响应体写日志时的截断长度：保留足够排障的信息，又不至于把整页 HTML 灌进日志
_LOG_BODY_LIMIT = 500

# 用户可见文本的截断长度：这些文本可能被直接回给 QQ 群，必须短
_TEXT_LIMIT = 100

# HTML 响应的一句话说明（多数情况是地址/反向代理路由配错，正文对用户毫无意义）
_HTML_SUMMARY = "响应是网页 HTML（疑似后端地址或反向代理路由配错，内容已省略）"

# 「200 但不是 JSON」的说明
_NOT_JSON_SUMMARY = "后端返回的不是 JSON 数据（疑似网页或网关错误页）"


def set_service_api_key(api_key: str):
    """注入服务间认证密钥（应与 kahunabot config.toml 的 [SERVICE] API_KEY 一致）"""
    global _SERVICE_API_KEY
    _SERVICE_API_KEY = (api_key or "").strip()


def _build_headers() -> dict:
    """构造请求头；未配置密钥时不携带该头（服务端将按未授权处理）"""
    headers = {}
    if _SERVICE_API_KEY:
        headers["X-Internal-API-Key"] = _SERVICE_API_KEY
    return headers


def clip_text(text, limit: int = _TEXT_LIMIT) -> str:
    """把文本压成一行并截断到 limit 字符（用于回给用户的提示语）。"""
    text = "" if text is None else str(text)
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


def _looks_like_html(text: str) -> bool:
    head = text[:200].lower()
    return (
        head.startswith("<!doctype")
        or head.startswith("<html")
        or "<head" in head
        or "<body" in head
        or "<script" in head
    )


def _extract_json_message(text: str) -> str:
    """从后端 JSON 错误体里取 message 字段（后端约定 {'status':..,'message':..}）。"""
    if not text.startswith("{"):
        return ""
    try:
        data = json.loads(text)
    except Exception:
        return ""
    if isinstance(data, dict):
        for key in ("message", "msg", "error", "detail"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _summarize_body(body: str) -> str:
    """把响应体压成一句安全摘要：HTML 打码、JSON 取 message、其它截断。

    摘要可能被回给用户，因此绝不放整段响应体——后端/网关返回的 HTML 页面
    动辄几十 KB（Cloudflare 错误页、nginx 错误页、SPA 回退页）。
    """
    text = (body or "").strip()
    if not text:
        return ""
    if _looks_like_html(text):
        return _HTML_SUMMARY
    message = _extract_json_message(text)
    if message:
        return clip_text(message)
    return clip_text(text)


class ApiError(ValueError):
    """后端接口调用失败（状态码非 200，或 200 但返回的不是 JSON）。

    ⚠️ 响应体**不提供给用户**，只用于写日志：QQ 群里的报错一律是固定文案
    （见 src/event.py 的 api_error_for_user）。这里仍要收口，是因为
    logger.error(f"...: {e}") 到处在用，异常文本不能夹带几十 KB 的 HTML。
    因此 str(e) 只含「状态码 + 极短摘要」，原始响应体放在 .body，
    仅供 _log_failure() 截断后写日志。
    """

    def __init__(self, status: int, body: str = "", url: str = "", summary: str = ""):
        self.status = int(status or 0)
        self.url = url or ""
        self.body = body or ""
        self.summary = summary or _summarize_body(self.body)
        super().__init__(self._build_message())

    def _build_message(self) -> str:
        if self.summary:
            return f"HTTP {self.status}, message: {self.summary}"
        return f"HTTP {self.status}"


def _log_failure(method: str, api_url: str, status: int, body: str) -> None:
    """完整（截断后的）响应体只写日志，绝不回给用户。"""
    logger.warning(
        f"{method} {api_url} 返回 HTTP {status}，"
        f"响应体(截断 {_LOG_BODY_LIMIT} 字): {clip_text(body, _LOG_BODY_LIMIT)}"
    )


async def _read_text(resp) -> str:
    """统一按 UTF-8 读取响应体（后端固定 UTF-8，避免 charset 猜测出错）。"""
    raw = await resp.read()
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("utf-8", errors="replace")


def _parse_json_or_raise(text: str, api_url: str, status: int, method: str = "POST"):
    try:
        return json.loads(text)
    except Exception:
        _log_failure(method, api_url, status, text)
        raise ApiError(status, text, api_url, summary=_NOT_JSON_SUMMARY)


def _build_url(host: str, path: str) -> str:
    if host.startswith("http://") or host.startswith("https://"):
        return f"{host}{path}"
    return f"http://{host}{path}"


async def get_json(host: str, path: str, timeout: int):
    api_url = _build_url(host, path)

    async with aiohttp.ClientSession() as session:
        async with session.get(
            api_url,
            headers=_build_headers(),
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            text = await _read_text(resp)
            if resp.status != 200:
                _log_failure("GET", api_url, resp.status, text)
                raise ApiError(resp.status, text, api_url)
            return _parse_json_or_raise(text, api_url, resp.status, "GET")


async def post_json(host: str, path: str, payload: dict, timeout: int):
    api_url = _build_url(host, path)

    logger.debug(f"POST {api_url}, payload: {payload}")
    async with aiohttp.ClientSession() as session:
        async with session.post(
            api_url,
            json=payload,
            headers=_build_headers(),
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            text = await _read_text(resp)
            if resp.status != 200:
                # 后端/网关的原始页面只进日志；异常里只留状态码 + 短摘要
                _log_failure("POST", api_url, resp.status, text)
                raise ApiError(resp.status, text, api_url)
            return _parse_json_or_raise(text, api_url, resp.status)


async def api_info(host: str, api_id: str):
    return await post_json(
        host,
        "/api/astrbot/kahunasystem/api/info",
        {"args": api_id},
        timeout=10,
    )


async def api_run(
    host: str, api_id: str, args: dict, qq: str = None, access_token: str = None
):
    return await post_json(
        host,
        "/api/astrbot/kahunasystem/api/run",
        {"api_id": api_id, "args": args, "qq": qq, "access_token": access_token},
        timeout=300,
    )


async def api_price_detail(host: str, type_name: str, access_token: str = None):
    return await api_run(
        host,
        "market_price_detail",
        {"type_name": type_name},
        None,
        access_token,
    )


async def api_frt_price_detail(host: str, type_name: str, access_token: str = None):
    return await api_run(
        host,
        "market_price_detail",
        {"type_name": type_name, "market_zone": "frt"},
        None,
        access_token,
    )


async def api_type_cost(
    host: str, type_name: str, user_name: str, plan_name: str, access_token: str = None
):
    return await api_run(
        host,
        "market_type_cost",
        {
            "type_name": type_name,
            "user_name": user_name,
            "plan_name": plan_name,
        },
        None,
        access_token,
    )


async def api_qq_bind(host: str, qq, uuid: str):
    """QQ 号绑定 kahunasystem 账号（走 astrbot 蓝图，需携带服务密钥）。"""
    api_url = _build_url(host, "/api/astrbot/kahunasystem/qq/bind")
    async with aiohttp.ClientSession() as session:
        async with session.post(
            api_url,
            json={"QQ": qq, "uuid": uuid},
            headers=_build_headers(),
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            text = await _read_text(resp)
            data = None
            try:
                data = json.loads(text)
            except Exception:
                data = {}
            if not isinstance(data, dict):
                data = {}
            data.setdefault("status", resp.status)
            return data


async def api_qq_vip_state(host: str, qq):
    """查询 QQ 号绑定状态（走 astrbot 蓝图，需携带服务密钥）。"""
    api_url = _build_url(host, "/api/astrbot/kahunasystem/qq/vip")
    async with aiohttp.ClientSession() as session:
        async with session.post(
            api_url,
            json={"QQ": qq},
            headers=_build_headers(),
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            text = await _read_text(resp)
            data = None
            try:
                data = json.loads(text)
            except Exception:
                data = {}
            if not isinstance(data, dict):
                data = {}
            data.setdefault("status", resp.status)
            return data


async def api_zkb_get_character_data(host: str, character_id: int):
    return await get_json(
        "https://zkillboard.com/api/stats/characterID",
        f"/{character_id}/",
        timeout=10,
    )


async def api_zkb_get_corporation_data(host: str, corporation_id: int):
    return await get_json(
        "https://zkillboard.com/api/stats/corporationID",
        f"/{corporation_id}/",
        timeout=10,
    )


async def api_esi_name2id(search_str: str):
    return await post_json(
        "https://esi.evetech.net",
        "/latest/universe/ids/",
        payload=[search_str],
        timeout=10,
    )
