"""入群申请自动审核。

收到 OneBot 的群请求（``sub_type=add``）时，检查申请人 QQ 是否在 SeAT 名单里：

- 在名单里 → 通过；
- 不在名单里 → 拒绝，理由固定为「您不是我司成员，请联系管理员。」

只在群号白名单内生效；名单为空或没有任何 QQ 时不做任何处理（防止误拒）。
支持调试模式：只记录判定结果，不实际通过/拒绝。
"""

from __future__ import annotations

from astrbot.api import logger

from .seat_config import get as get_cfg
from .seat_kick import configured_qq_whitelist
from .seat_rename import find_aiocqhttp_bot
from .seat_roster import load_roster

# 拒绝时给申请人的默认理由（可在插件配置中覆盖）
DEFAULT_REJECT_REASON = "您不是我司成员，请联系管理员。"


def _cfg(plugin, key: str, default):
    return get_cfg(plugin, "seat_join", key, default)


def _reject_reason(plugin) -> str:
    """读取自定义拒绝理由，空值时回退默认文案。"""
    value = _cfg(plugin, "reject_reason", DEFAULT_REJECT_REASON)
    text = str(value or "").strip()
    return text or DEFAULT_REJECT_REASON


def configured_group_ids(plugin) -> list[str]:
    """读取入群审核的群号白名单。"""
    raw = _cfg(plugin, "group_ids", [])
    if isinstance(raw, str):
        return [part.strip() for part in raw.split(",") if part.strip()]
    if isinstance(raw, (list, tuple)):
        return [str(part).strip() for part in raw if str(part).strip()]
    return []


async def handle_group_request(plugin, raw_event) -> dict | None:
    """处理一条群请求事件；不是入群申请时返回 None。"""
    if not isinstance(raw_event, dict):
        return None
    if raw_event.get("post_type") != "request":
        return None
    if raw_event.get("request_type") != "group":
        return None
    if raw_event.get("sub_type") != "add":
        return None

    if not bool(_cfg(plugin, "enabled", True)):
        return None

    group_id = str(raw_event.get("group_id") or "").strip()
    user_id = str(raw_event.get("user_id") or "").strip()
    flag = raw_event.get("flag")
    if not group_id or not user_id or not flag:
        return None

    allowed_groups = configured_group_ids(plugin)
    if allowed_groups and group_id not in allowed_groups:
        return None

    roster = await load_roster(plugin)
    if not roster or not roster.get("items"):
        logger.warning("入群审核跳过：还没有成员名单")
        return None

    allowed_qqs = {str(item.get("qq_id") or "").strip() for item in roster["items"]}
    allowed_qqs.discard("")
    if not allowed_qqs:
        logger.warning("入群审核跳过：名单里没有任何 QQ 号")
        return None

    # 踢人白名单里的号同样放行：它们本来就被保护，不能在入群时被拒
    kick_whitelist = configured_qq_whitelist(plugin)
    approve = user_id in allowed_qqs or user_id in kick_whitelist
    reason = "" if approve else _reject_reason(plugin)
    debug = bool(_cfg(plugin, "debug", False))

    result = {
        "user_id": user_id,
        "group_id": group_id,
        "approve": approve,
        "debug": debug,
    }

    if debug:
        logger.info(
            "SeAT 调试（入群审核）：QQ %s 申请加入 %s -> %s",
            user_id,
            group_id,
            "通过" if approve else "拒绝",
        )
        return result

    bot = find_aiocqhttp_bot(plugin)
    if bot is None:
        logger.warning("入群审核跳过：没有可用的 aiocqhttp 平台实例")
        return None

    try:
        await bot.call_action(
            "set_group_add_request",
            flag=flag,
            sub_type="add",
            approve=approve,
            reason=reason,
        )
    except Exception as exc:
        logger.warning(f"入群审核处理失败（QQ {user_id}）：{exc}")
        result["error"] = str(exc)
        return result

    logger.info(
        "入群审核：QQ %s 申请加入 %s -> %s",
        user_id,
        group_id,
        "通过" if approve else "拒绝",
    )
    return result
