"""按 SeAT 名单同步群名片：``SETCR-<NickName>/<MainCharacter>``。

设计要点
--------
- 只处理「名单里有 QQ 号、且该 QQ 在群里」的成员；
- 只在当前名片与期望值不一致时才调用改名，避免无意义操作；
- 每次改名之间随机等待数秒，并限制单轮改名数量，降低触发腾讯风控的概率；
- 单条失败（权限不足、风控等）只记录日志，不中断流程，下一轮会重试；
- 通过 AstrBot 的 aiocqhttp 平台实例拿到 CqHttp 客户端。只有 OneBot 适配器
  支持 ``set_group_card``，QQ 官方 API 没有修改群名片的能力。
"""

from __future__ import annotations

import asyncio
import random
from datetime import datetime

from astrbot.api import logger

from .seat_roster import load_roster

# 最近一次同步结果（KV）
RENAME_RESULT_KEY = "seat:rename:last"

# SeAT 里没填昵称时使用的固定占位词
PLACEHOLDER_NICK = "未知"

# 改名间隔（秒）：随机取值，模拟人工操作节奏
RENAME_DELAY_MIN = 4.0
RENAME_DELAY_MAX = 9.0

# 单轮默认最多改多少个
DEFAULT_MAX_CHANGES = 20


def build_expected_card(nickname: str, character: str) -> str:
    """生成期望的群名片：SETCR-昵称/角色名。"""
    nick = (nickname or "").strip() or PLACEHOLDER_NICK
    character = (character or "").strip()
    return f"SETCR-{nick}/{character}"


def _cfg(plugin, key: str, default):
    config = getattr(plugin, "config", None)
    if config is None:
        return default
    try:
        value = config.get(key)
    except Exception:
        return default
    return default if value is None else value


def find_aiocqhttp_bot(plugin):
    """找到 aiocqhttp 平台实例对应的 CqHttp 客户端。"""
    manager = getattr(plugin.context, "platform_manager", None)
    if manager is None:
        logger.warning("AstrBot 没有 platform_manager，无法定位平台实例")
        return None
    try:
        insts = manager.get_insts()
    except Exception as exc:
        logger.warning(f"读取平台实例列表失败：{exc}")
        return None
    for inst in insts:
        try:
            if inst.meta().name == "aiocqhttp":
                return inst.get_client()
        except Exception:
            continue
    return None


def configured_group_ids(plugin) -> list[str]:
    """读取群名片白名单（兼容 list 与逗号分隔字符串两种写法）。"""
    raw = _cfg(plugin, "seat_rename_group_ids", [])
    if isinstance(raw, str):
        return [part.strip() for part in raw.split(",") if part.strip()]
    if isinstance(raw, (list, tuple)):
        return [str(part).strip() for part in raw if str(part).strip()]
    return []


async def sync_group_cards(
    plugin, roster: dict | None = None, group_id: str = ""
) -> dict:
    """按名单同步单个群的群名片，返回统计结果。"""
    group_id = str(group_id or "").strip()
    if not group_id:
        raise RuntimeError("没有指定要同步的群号")

    try:
        max_changes = int(_cfg(plugin, "seat_rename_limit", DEFAULT_MAX_CHANGES))
    except (TypeError, ValueError):
        max_changes = DEFAULT_MAX_CHANGES

    if roster is None:
        roster = await load_roster(plugin)
    if not roster or not roster.get("items"):
        raise RuntimeError("还没有成员名单，请先刷新一次")

    bot = find_aiocqhttp_bot(plugin)
    if bot is None:
        raise RuntimeError("没有可用的 aiocqhttp 平台实例，无法修改群名片")

    # 调试模式：只计算与记录，不实际调用改名接口
    debug = bool(_cfg(plugin, "seat_rename_debug", False))

    try:
        members = await bot.call_action(
            "get_group_member_list", group_id=int(group_id)
        )
    except Exception as exc:
        raise RuntimeError(f"获取群成员失败：{exc}")

    member_map: dict[str, dict] = {}
    for member in members or []:
        uid = str(member.get("user_id") or "").strip()
        if uid:
            member_map[uid] = {
                "card": str(member.get("card") or ""),
                "role": str(member.get("role") or ""),
            }

    stats = {
        "group_id": group_id,
        "debug": debug,
        "checked": 0,
        "matched": 0,
        "changed": 0,
        "planned": 0,
        "failed": 0,
        "skipped": 0,
        "skipped_role": 0,
        "limited": 0,
        "changes": [],
        "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    for item in roster["items"]:
        qq = str(item.get("qq_id") or "").strip()
        if not qq or qq not in member_map:
            stats["skipped"] += 1
            continue

        info = member_map[qq]
        # 管理员与群主不动：避免影响管理权限，也避免逆反操作
        if info["role"] in ("owner", "admin"):
            stats["skipped_role"] += 1
            continue

        expected = build_expected_card(item.get("nickname", ""), item.get("character", ""))
        current = info["card"]
        stats["checked"] += 1

        if current == expected:
            stats["matched"] += 1
            continue

        if stats["changed"] + stats["planned"] >= max_changes:
            stats["limited"] += 1
            continue

        # 调试模式：只记录预计操作，不请求改名接口，也不做风控等待
        if debug:
            stats["planned"] += 1
            stats["changes"].append(
                {"qq": qq, "from": current, "to": expected, "applied": False}
            )
            logger.info(
                "SeAT 调试：将改群名片 QQ %s：%s -> %s",
                qq,
                current or "(空)",
                expected,
            )
            continue

        try:
            await bot.call_action(
                "set_group_card",
                group_id=int(group_id),
                user_id=int(qq),
                card=expected,
            )
            stats["changed"] += 1
            stats["changes"].append(
                {"qq": qq, "from": current, "to": expected, "applied": True}
            )
        except Exception as exc:
            stats["failed"] += 1
            logger.warning(f"SeAT 改名失败（QQ {qq}）：{exc}")

        # 无论成功失败都拉开间隔，避免连续请求触发风控
        await asyncio.sleep(random.uniform(RENAME_DELAY_MIN, RENAME_DELAY_MAX))

    await plugin.put_kv_data(RENAME_RESULT_KEY, stats)
    logger.info(
        "SeAT 群名片同步%s：检查 %d，已符合 %d，改名 %d，预计改名 %d，失败 %d，管理员跳过 %d，超限跳过 %d",
        "（调试模式）" if debug else "",
        stats["checked"],
        stats["matched"],
        stats["changed"],
        stats["planned"],
        stats["failed"],
        stats["skipped_role"],
        stats["limited"],
    )
    return stats


async def sync_all_groups(plugin, roster: dict | None = None) -> list[dict]:
    """遍历白名单里的群，逐个同步群名片。"""
    group_ids = configured_group_ids(plugin)
    if not group_ids:
        raise RuntimeError("未配置群名片白名单（seat_rename_group_ids）")

    if roster is None:
        roster = await load_roster(plugin)
    if not roster or not roster.get("items"):
        raise RuntimeError("还没有成员名单，请先刷新一次")

    results: list[dict] = []
    for group_id in group_ids:
        results.append(await sync_group_cards(plugin, roster=roster, group_id=group_id))
    return results


async def load_last_result(plugin) -> dict | None:
    try:
        return await plugin.get_kv_data(RENAME_RESULT_KEY, None)
    except Exception:
        return None
