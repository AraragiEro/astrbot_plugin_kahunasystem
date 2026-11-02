"""MCP 客户端：插件取数据的唯一入口（SSE 传输）。

背景
----
``/api/astrbot/*``（api_info 取 token → api_run 按 api_id 转发）已全面日落淘汰，
查价 / 成本等**读能力**统一由 Kahuna MCP 中间层提供，插件指令直接调用对应 MCP tool。
写操作（QQ 绑定）不走 MCP，见 ``src/api_client.py``。

为什么不用 mcp SDK
------------------
AstrBot 对插件依赖有「核心依赖版本保护」：新增 mcp / httpx 之类的固定版本依赖
容易与核心环境冲突（历史踩坑：pyppeteer 依赖 websockets<11 导致整份依赖被拦截）。
因此这里只用 aiohttp（AstrBot 核心已提供）手写最小 SSE + JSON-RPC 客户端。

传输流程（MCP SSE）
------------------
1. ``GET <base>/sse``，服务端先回一个 ``event: endpoint`` 事件，给出消息投递路径；
2. 之后的 JSON-RPC 请求 POST 到该 endpoint，HTTP 只回 202，**响应从 SSE 流返回**；
3. 单会话内串行调用（指令频率低），按 JSON-RPC id 匹配请求与响应。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import aiohttp
from astrbot.api import logger

# 与服务端 mcp SDK 协商的协议版本（服务端支持 2024-11-05 / 2025-03-26 / 2025-06-18）
PROTOCOL_VERSION = "2025-06-18"

DEFAULT_MCP_URL = "http://127.0.0.1:9000/sse"

_CONNECT_TIMEOUT = 10
_ENDPOINT_TIMEOUT = 15
_CALL_TIMEOUT = 120
# 单行 SSE 数据可能很长（价格历史一年约几十 KB），放宽读取缓冲避免 Line is too long
_READ_BUF = 4 * 1024 * 1024


class McpError(RuntimeError):
    """MCP 调用失败（连接 / 协议 / 工具返回错误）。

    消息保持简短且不含响应正文，可直接作为给用户的提示语。
    """


def _normalize_base(url: str) -> str:
    base = (url or "").strip().rstrip("/")
    if not base:
        raise McpError("未配置 MCP 服务地址")
    if base.endswith("/sse"):
        base = base[: -len("/sse")]
    if not (base.startswith("http://") or base.startswith("https://")):
        base = f"http://{base}"
    return base


def _extract_text(result: dict) -> str:
    """从 tools/call 结果的 content 数组里拼出文本内容。"""
    parts: list[str] = []
    for item in result.get("content") or []:
        if (
            isinstance(item, dict)
            and item.get("type") == "text"
            and isinstance(item.get("text"), str)
        ):
            parts.append(item["text"])
    return "\n".join(parts).strip()


class KahunaMcpClient:
    """单次会话的 MCP 客户端（异步上下文管理器）。"""

    def __init__(self, url: str = DEFAULT_MCP_URL, call_timeout: float = _CALL_TIMEOUT):
        self._base = _normalize_base(url)
        self._call_timeout = call_timeout
        self._session: aiohttp.ClientSession | None = None
        self._resp: aiohttp.ClientResponse | None = None
        self._reader: asyncio.Task | None = None
        self._endpoint: str | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._lock = asyncio.Lock()
        self._next_id = 0

    # ------------------------------------------------------------ 生命周期

    async def __aenter__(self) -> "KahunaMcpClient":
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(
                total=None, sock_connect=_CONNECT_TIMEOUT, sock_read=None
            ),
            read_bufsize=_READ_BUF,
        )
        try:
            self._resp = await self._session.get(
                f"{self._base}/sse", headers={"Accept": "text/event-stream"}
            )
            if self._resp.status != 200:
                raise McpError(f"MCP 服务不可用（HTTP {self._resp.status}）")

            self._endpoint = await asyncio.wait_for(
                self._wait_endpoint(), timeout=_ENDPOINT_TIMEOUT
            )
            self._reader = asyncio.create_task(self._read_loop())
            await self._initialize()
            return self
        except Exception:
            await self._close()
            raise

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self._close()

    async def _close(self) -> None:
        if self._reader is not None and not self._reader.done():
            self._reader.cancel()
            try:
                await self._reader
            except (asyncio.CancelledError, Exception):
                pass
        self._reader = None

        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(McpError("MCP 连接已关闭"))
        self._pending.clear()

        if self._resp is not None:
            try:
                self._resp.release()
            except Exception:
                pass
            self._resp = None
        if self._session is not None:
            try:
                await self._session.close()
            except Exception:
                pass
            self._session = None

    # ------------------------------------------------------------ SSE 解析

    async def _read_event(self) -> tuple[str | None, str]:
        """从 SSE 流读一个完整事件，返回 (event, data)；流结束返回 (None, "")。"""
        event = "message"
        data_lines: list[str] = []
        while True:
            line = await self._resp.content.readline()
            if not line:
                return None, ""
            text = line.decode("utf-8", errors="replace").rstrip("\r\n")
            if not text:
                if data_lines:
                    return event, "\n".join(data_lines)
                event = "message"
                continue
            if text.startswith(":"):
                continue
            field, _, value = text.partition(":")
            if value.startswith(" "):
                value = value[1:]
            if field == "event":
                event = value
            elif field == "data":
                data_lines.append(value)

    async def _wait_endpoint(self) -> str:
        while True:
            event, data = await self._read_event()
            if event is None:
                raise McpError("MCP 服务未返回 endpoint（连接被关闭）")
            if event == "endpoint" and data.strip():
                return data.strip()

    async def _read_loop(self) -> None:
        try:
            while True:
                event, data = await self._read_event()
                if event is None:
                    break
                if event != "message" or not data:
                    continue
                try:
                    message = json.loads(data)
                except json.JSONDecodeError:
                    logger.warning("MCP 响应不是合法 JSON，已忽略")
                    continue
                msg_id = message.get("id")
                future = (
                    self._pending.pop(msg_id, None)
                    if isinstance(msg_id, int)
                    else None
                )
                if future is not None and not future.done():
                    future.set_result(message)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # 单次会话内不重连，交由调用方重试
            logger.warning(f"MCP 事件流中断: {e}")
        finally:
            for future in list(self._pending.values()):
                if not future.done():
                    future.set_exception(McpError("MCP 连接已断开"))
            self._pending.clear()

    # ------------------------------------------------------------ JSON-RPC

    async def _post(self, payload: dict) -> None:
        endpoint = self._endpoint
        if not endpoint:
            raise McpError("MCP 连接尚未建立")
        if endpoint.startswith("http://") or endpoint.startswith("https://"):
            url = endpoint
        else:
            prefix = "" if endpoint.startswith("/") else "/"
            url = f"{self._base}{prefix}{endpoint}"

        async with self._session.post(url, json=payload) as resp:
            if resp.status not in (200, 202):
                body = await resp.text()
                logger.warning(
                    f"MCP POST {payload.get('method')} 返回 HTTP {resp.status}："
                    f"{body[:300]}"
                )
                raise McpError(f"MCP 请求失败（HTTP {resp.status}）")

    async def _request(
        self, method: str, params: dict | None = None, *, notify: bool = False
    ) -> dict | None:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params

        if notify:
            await self._post(payload)
            return None

        self._next_id += 1
        msg_id = self._next_id
        payload["id"] = msg_id

        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = future
        try:
            await self._post(payload)
            return await asyncio.wait_for(future, timeout=self._call_timeout)
        except asyncio.TimeoutError:
            raise McpError("MCP 调用超时")
        finally:
            self._pending.pop(msg_id, None)

    async def _initialize(self) -> None:
        message = await self._request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {
                    "name": "astrbot-plugin-kahunasystem",
                    "version": "1.0.0",
                },
            },
        )
        if message and message.get("error"):
            error = message["error"] or {}
            raise McpError(f"MCP 初始化失败: {error.get('message') or '未知错误'}")
        await self._request("notifications/initialized", notify=True)

    # ------------------------------------------------------------ 对外接口

    async def call_tool(
        self, name: str, arguments: dict | None = None, timeout: float | None = None
    ) -> dict:
        """调用一个 MCP tool，返回工具结果（dict）。

        工具返回的 dict 结果会原样还原；工具报错或 isError 抛 :class:`McpError`。
        """
        async with self._lock:
            message = await self._request(
                "tools/call",
                {"name": name, "arguments": arguments or {}},
            )
        if not message:
            raise McpError("MCP 未返回结果")
        if message.get("error"):
            error = message["error"] or {}
            raise McpError(
                f"工具 {name} 调用失败: {error.get('message') or '未知错误'}"
            )
        return self._unwrap(name, message.get("result") or {})

    @staticmethod
    def _unwrap(name: str, result: dict) -> dict:
        if result.get("isError"):
            detail = _extract_text(result)
            raise McpError(detail or f"工具 {name} 执行失败")

        structured = result.get("structuredContent")
        if isinstance(structured, dict):
            return structured

        text = _extract_text(result)
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return {"text": text}
        return parsed if isinstance(parsed, dict) else {"data": parsed}
