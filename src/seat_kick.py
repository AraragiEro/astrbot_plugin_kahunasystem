"""把不在 SeAT 名单里的群成员请出群聊。

安全约束（务必保留）
-------------------
- 名单为空或名单里没有任何 QQ 号时**直接拒绝执行**，防止误踢全员；
- 永远跳过 bot 自己、群主与管理员；
- 支持调试模式：只记录预计踢出的成员，不实际执行；
- 单轮数量限制 + 较长的随机间隔，降低触发风控的概率。
"""

from __future__ import annotations

import asyncio
import json
import random
import re
from datetime import datetime

from astrbot.api import logger

from .seat_config import get as get_cfg
from .seat_rename import find_aiocqhttp_bot
from .seat_roster import load_roster

# 最近一次清理结果（KV）
KICK_RESULT_KEY = "seat:kick:last"

# 踢人间隔（秒）：比改名更保守
KICK_DELAY_MIN = 8.0
KICK_DELAY_MAX = 15.0

# 单轮默认最多踢几个
DEFAULT_MAX_KICKS = 20

# 受保护的群角色
PROTECTED_ROLES = ("owner", "admin")


def _cfg(plugin, key: str, default):
    return get_cfg(plugin, "seat_kick", key, default)


def configured_kick_group_ids(plugin) -> list[str]:
    """读取踢人白名单（兼容 list 与逗号分隔字符串）。"""
    raw = _cfg(plugin, "group_ids", [])
    if isinstance(raw, str):
        return [part.strip() for part in raw.split(",") if part.strip()]
    if isinstance(raw, (list, tuple)):
        return [str(part).strip() for part in raw if str(part).strip()]
    return []


def configured_qq_whitelist(plugin) -> set[str]:
    """读取 QQ 白名单：这些号即使不在 SeAT 名单里也不会被踢。

    配置是多行文本（一行一个 QQ），也兼容逗号/空格分隔和旧的 list 形式。
    """
    raw = _cfg(plugin, "qq_whitelist", "")
    if isinstance(raw, str):
        parts = re.split(r"[\s,，]+", raw)
    elif isinstance(raw, (list, tuple)):
        parts = [str(item) for item in raw]
    else:
        parts = []
    return {part.strip() for part in parts if part.strip()}


async def kick_outside_roster(plugin, roster: dict | None = None, group_id: str = "") -> dict:
    """清理单个群里不在名单中的成员，返回统计结果。"""
    group_id = str(group_id or "").strip()
    if not group_id:
        raise RuntimeError("没有指定要清理的群号")

    debug = bool(_cfg(plugin, "debug", False))
    try:
        max_kicks = int(_cfg(plugin, "limit", DEFAULT_MAX_KICKS))
    except (TypeError, ValueError):
        max_kicks = DEFAULT_MAX_KICKS

    if roster is None:
        roster = await load_roster(plugin)
    if not roster or not roster.get("items"):
        raise RuntimeError("还没有成员名单，请先刷新一次")

    allowed = {str(item.get("qq_id") or "").strip() for item in roster["items"]}
    allowed.discard("")
    if not allowed:
        # 名单里一个 QQ 都没有时，任何群成员都会被判定为「不在名单」，
        # 这必然是数据问题，不能继续执行。
        raise RuntimeError("名单里没有任何 QQ 号，拒绝执行踢人（防止误踢全员）")

    qq_whitelist = configured_qq_whitelist(plugin)

    bot = find_aiocqhttp_bot(plugin)
    if bot is None:
        raise RuntimeError("没有可用的 aiocqhttp 平台实例，无法踢人")

    self_id = str(getattr(bot, "self_id", "") or "")
    try:
        members = await bot.call_action("get_group_member_list", group_id=int(group_id))
    except Exception as exc:
        raise RuntimeError(f"获取群成员失败：{exc}")

    stats = {
        "group_id": group_id,
        "debug": debug,
        "total": 0,
        "kept": 0,
        "kicked": 0,
        "planned": 0,
        "failed": 0,
        "skipped_role": 0,
        "skipped_self": 0,
        "skipped_whitelist": 0,
        "limited": 0,
        "removed": [],
        "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    for member in members or []:
        uid = str(member.get("user_id") or "").strip()
        if not uid:
            continue
        stats["total"] += 1

        if uid == self_id:
            stats["skipped_self"] += 1
            continue

        if str(member.get("role") or "") in PROTECTED_ROLES:
            stats["skipped_role"] += 1
            continue

        # QQ 白名单：即使不在 SeAT 名单里也保留
        if uid in qq_whitelist:
            stats["skipped_whitelist"] += 1
            continue

        if uid in allowed:
            stats["kept"] += 1
            continue

        if stats["kicked"] + stats["planned"] >= max_kicks:
            stats["limited"] += 1
            continue

        nickname = str(member.get("card") or member.get("nickname") or "")

        # 调试模式：只记录预计踢出，不请求踢人接口，也不做风控等待
        if debug:
            stats["planned"] += 1
            stats["removed"].append(
                {"qq": uid, "nickname": nickname, "applied": False}
            )
            logger.info("SeAT 调试：将踢出 QQ %s（%s）", uid, nickname or "(无名片)")
            continue

        try:
            await bot.call_action(
                "set_group_kick",
                group_id=int(group_id),
                user_id=int(uid),
                reject_add_request=False,
            )
            stats["kicked"] += 1
            stats["removed"].append(
                {"qq": uid, "nickname": nickname, "applied": True}
            )
        except Exception as exc:
            stats["failed"] += 1
            logger.warning(f"SeAT 踢人失败（QQ {uid}）：{exc}")

        # 踢人比改名敏感，间隔拉长
        await asyncio.sleep(random.uniform(KICK_DELAY_MIN, KICK_DELAY_MAX))

    await plugin.put_kv_data(KICK_RESULT_KEY, stats)

    if debug:
        # 预计踢出的名单：QQ 一行一个（可直接粘贴到白名单），再给 QQ -> 名字 的对照
        removed = stats["removed"]
        removed_qqs = [item.get("qq", "") for item in removed]
        logger.info(
            "SeAT 调试：预计踢出 QQ（一行一个，可直接粘贴到 seat_kick_qq_whitelist）：\n%s",
            "\n".join(removed_qqs) or "(无)",
        )
        logger.info(
            "SeAT 调试：预计踢出 QQ -> 名字：%s",
            json.dumps(
                {
                    item.get("qq", ""): item.get("nickname", "")
                    for item in removed
                },
                ensure_ascii=False,
            ),
        )

    logger.info(
        "SeAT 清理群成员%s：群内 %d 人，保留 %d，踢出 %d，预计踢出 %d，失败 %d，管理员跳过 %d，白名单跳过 %d，超限跳过 %d",
        "（调试模式）" if debug else "",
        stats["total"],
        stats["kept"],
        stats["kicked"],
        stats["planned"],
        stats["failed"],
        stats["skipped_role"],
        stats["skipped_whitelist"],
        stats["limited"],
    )
    return stats


async def kick_all_groups(plugin, roster: dict | None = None) -> list[dict]:
    """遍历白名单里的群，逐个清理不在名单中的成员。"""
    group_ids = configured_kick_group_ids(plugin)
    if not group_ids:
        raise RuntimeError("未配置踢人白名单（seat_kick_group_ids）")

    if roster is None:
        roster = await load_roster(plugin)
    if not roster or not roster.get("items"):
        raise RuntimeError("还没有成员名单，请先刷新一次")

    results: list[dict] = []
    for group_id in group_ids:
        results.append(await kick_outside_roster(plugin, roster=roster, group_id=group_id))
    return results


async def load_last_kick(plugin) -> dict | None:
    try:
        return await plugin.get_kv_data(KICK_RESULT_KEY, None)
    except Exception:
        return None
