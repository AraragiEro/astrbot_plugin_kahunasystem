"""插件剩余的 HTTP 客户端：只保留 QQ 绑定。

``/api/astrbot/*`` 那套（api_info 取 token → api_run 按 api_id 转发）已全面日落淘汰：
查价 / 成本等**读能力**改由 MCP 中间层提供（见 ``src/mcp_client.py``）。

绑定是**一次性写操作**，按约定不走 MCP，因此保留这一条最小 HTTP 调用：

- ``POST /api/plugin/qq/bind``（服务密钥认证，请求头 ``X-Internal-API-Key``）

错误处理原则不变：**响应体绝不回给用户**（后端/网关出错时是整页 HTML，
2026-10-17 线上事故：整页 doctype 被灌进 QQ 群），只写日志；
异常文本只含「状态码 + 极短摘要」。
"""

import json

import aiohttp
from astrbot.api import logger

# 服务间认证密钥：由 main.py 在插件初始化时通过 set_service_api_key() 注入。
# QQ 机器人无法交互式登录，改用共享服务密钥
# （与后端 config.toml 的 [SERVICE] API_KEY 一致）。
_SERVICE_API_KEY = ""

# 响应体写日志时的截断长度：保留足够排障的信息，又不至于把整页 HTML 灌进日志
_LOG_BODY_LIMIT = 500

# 用户可见文本的截断长度：这些文本可能被直接回给 QQ 群，必须短
_TEXT_LIMIT = 100

_HTML_SUMMARY = "响应是网页 HTML（疑似后端地址或反向代理路由配错，内容已省略）"

QQ_BIND_PATH = "/api/plugin/qq/bind"


def set_service_api_key(api_key: str) -> None:
    """注入服务间认证密钥（应与后端 config.toml 的 [SERVICE] API_KEY 一致）"""
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
    """把响应体压成一句安全摘要：HTML 打码、JSON 取 message、其它截断。"""
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
    （见 src/event.py 的 api_error_for_user）。
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


def _build_url(host: str, path: str) -> str:
    if host.startswith("http://") or host.startswith("https://"):
        return f"{host}{path}"
    return f"http://{host}{path}"


async def bind_qq(host: str, qq, bind_uuid: str) -> dict:
    """消费 Web 端生成的绑定指令，把 QQ 号绑定到账号（走 /api/plugin，需服务密钥）。"""
    api_url = _build_url(host, QQ_BIND_PATH)

    async with aiohttp.ClientSession() as session:
        async with session.post(
            api_url,
            json={"QQ": qq, "uuid": bind_uuid},
            headers=_build_headers(),
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            raw = await resp.read()
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                text = raw.decode("utf-8", errors="replace")

            if resp.status != 200:
                _log_failure("POST", api_url, resp.status, text)
                raise ApiError(resp.status, text, api_url)

            try:
                data = json.loads(text)
            except Exception:
                _log_failure("POST", api_url, resp.status, text)
                raise ApiError(
                    resp.status,
                    text,
                    api_url,
                    summary="后端返回的不是 JSON 数据（疑似网页或网关错误页）",
                )

            if not isinstance(data, dict):
                data = {}
            data.setdefault("status", resp.status)
            return data
