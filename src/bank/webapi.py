# -*- coding: utf-8 -*-
"""成员银行 Web 接口：单入口 action 分发。

与 AstrBot 视图 bridge 对接：

    bridge.apiPost("bank", { action, ... })
    -> POST /api/v1/plugins/extensions/astrbot_plugin_kahunasystem/bank

把分发逻辑放在这里而不是 ``main.py``，好处是改接口不必动插件入口文件。
"""
from __future__ import annotations

from astrbot.api import logger
from astrbot.api.web import json_response, request

from . import handlers as bank_handlers
from .pap import PapFetchError, fetch_pap_month


def make_bank_api(plugin):
    """构造 ``context.register_web_api`` 用的 handler（闭包持有 plugin）。"""

    async def handler():
        payload = await request.json(default={}) or {}
        action = str(payload.get("action") or "").strip()
        try:
            result = await dispatch(plugin, action, payload)
        except Exception as exc:  # 任何异常都转成结构化结果，前端不白屏
            logger.error(f"成员银行 Web 接口失败（{action}）：{exc}")
            result = {"ok": False, "message": f"操作失败：{exc}"}
        return json_response(result)

    return handler


async def _qq_map(plugin) -> dict[str, str]:
    """角色名 -> QQ；未填 QQ 的映射成空串，由导入逻辑标成 unclaimed。"""
    roster = await plugin._bank_roster()
    return {
        str(item.get("character") or ""): str(item.get("qq_id") or "")
        for item in (roster.get("items") or [])
    }


async def _pap_rows(plugin, period: str):
    """按月采集 PAP；失败时返回 ``{"ok": False, "message": ...}``。"""
    try:
        year, month = period.split("-")
        data = await fetch_pap_month(plugin, int(year), int(month))
    except PapFetchError as exc:
        return {"ok": False, "message": str(exc)}
    except Exception as exc:
        logger.error(f"采集 PAP 失败（{period}）：{exc}")
        return {"ok": False, "message": f"采集 PAP 失败：{exc}"}
    return data["rows"]


async def dispatch(plugin, action: str, payload: dict) -> dict:
    rt = plugin.bank_runtime
    op = "web"

    if action == "accounts":
        return await rt.run(bank_handlers.accounts_table)

    if action == "orders":
        return await rt.run(
            bank_handlers.pending_orders, str(payload.get("status") or "pending"))

    if action == "order_confirm":
        return await rt.run(
            bank_handlers.confirm_order,
            int(payload.get("order_id") or 0),
            int(payload.get("amount") or 0), op)

    if action == "order_reject":
        return await rt.run(
            bank_handlers.reject_order, int(payload.get("order_id") or 0), op)

    if action == "allowance":
        return await rt.run(
            bank_handlers.set_allowance,
            str(payload.get("qq_id") or ""),
            int(payload.get("amount") or 0), op,
            str(payload.get("period") or ""))

    if action == "adjust":
        return await rt.run(
            bank_handlers.adjust_balance,
            str(payload.get("qq_id") or ""),
            int(payload.get("amount") or 0), op)

    if action == "import_preview":
        return await rt.run(
            bank_handlers.preview_import, str(payload.get("text") or ""))

    if action == "import_apply":
        return await rt.run(
            bank_handlers.apply_import,
            str(payload.get("text") or ""),
            await _qq_map(plugin), op,
            str(payload.get("period") or ""))

    if action in ("settle_preview", "settle_apply"):
        period = str(payload.get("period") or "").strip()
        rows = await _pap_rows(plugin, period)
        if isinstance(rows, dict):
            return rows
        if action == "settle_preview":
            return await rt.run(
                bank_handlers.preview_settlement, period, rows)
        return await rt.run(
            bank_handlers.apply_settlement, period, rows, op,
            bool(payload.get("force")))

    return {"ok": False, "message": f"未知操作：{action}"}
