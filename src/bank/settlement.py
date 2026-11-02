# -*- coding: utf-8 -*-
"""PAP 结算引擎（纯函数，不依赖 AstrBot，可直接单测）。

口径来源：``plan/member_bank_design.md``。改规则先改那份文档，再改这里。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Iterable

# ---- 金额单位（ISK 整数，禁止浮点）----
ISK_E = 100_000_000  # 1e = 1 亿
ISK_B = 1_000_000_000  # 1b = 10 亿
BALANCE_CAP = 2 * ISK_B  # 余额硬上限 2b

# ---- 奖励档位 ----
PAP_START = 6  # 起发门槛（Start PAP）
PAP_STEP = 3  # 每 3 PAP 增加一档
REWARD_MIN = 2 * ISK_E  # 起步 2e
REWARD_STEP = 1 * ISK_E  # 每档 +1e
PAP_CAP = 15  # 15 PAP 封顶
REWARD_MAX = 5 * ISK_E  # 封顶 5e

# 三桶扣减顺序：当月奖励 -> 额外津贴 -> 余额
BUCKET_ORDER = ("monthly_reward", "allowance", "balance")


@dataclass(frozen=True)
class Account:
    """成员账户快照（不可变，所有变更返回新实例）。"""

    qq_id: str
    character: str = ""
    balance: int = 0  # 余额，<= BALANCE_CAP
    allowance: int = 0  # 额外津贴，独立桶，无上限、永不清零
    monthly_reward: int = 0  # 当月奖励，月末并入 balance 后归零
    status: str = "active"

    @property
    def total(self) -> int:
        """三桶合计（可支配总额）。"""
        return self.balance + self.allowance + self.monthly_reward


@dataclass(frozen=True)
class ConsumeResult:
    """一次消费的结果。不足时 ``ok=False`` 且账户不变（不做部分扣减）。"""

    ok: bool
    account: Account
    amount: int = 0
    breakdown: dict[str, int] | None = None
    shortfall: int = 0  # 拒绝时的缺口

    def as_breakdown(self) -> dict[str, int]:
        return dict(self.breakdown or {})


@dataclass(frozen=True)
class RolloverResult:
    """月末结转结果。``wasted`` 为超出 2b 被作废的金额。"""

    account: Account
    wasted: int = 0


def _to_int(value) -> int:
    """把 ISK 值转成整数，容忍字符串/浮点输入（ISK 无小数，四舍五入）。"""
    if value is None or value == "":
        return 0
    if isinstance(value, bool):
        raise TypeError("ISK 不接受布尔值")
    if isinstance(value, int):
        return value
    return int(round(float(str(value).replace(",", "").strip())))


def reward_for_pap(strat_pap) -> int:
    """按 Start PAP 计算当月奖励（ISK）。

    向下取整（9.5 -> 9）；<6 不发；>=15 封顶 5e。
    """
    try:
        raw = float(strat_pap or 0)
    except (TypeError, ValueError):
        raw = 0.0
    if raw != raw or raw in (float("inf"), float("-inf")):  # NaN / inf
        return 0
    pap = int(math.floor(raw))
    if pap < PAP_START:
        return 0
    if pap >= PAP_CAP:
        return REWARD_MAX
    steps = (pap - PAP_START) // PAP_STEP
    return min(REWARD_MAX, REWARD_MIN + steps * REWARD_STEP)


def grant_reward(account: Account, reward: int) -> Account:
    """发放当月奖励（先进 monthly_reward 桶，月末才并入余额并封顶）。"""
    reward = _to_int(reward)
    if reward <= 0:
        return account
    return replace(account, monthly_reward=account.monthly_reward + reward)


def rollover(account: Account) -> RolloverResult:
    """月末结转：monthly_reward 并入 balance 并封顶，津贴原样保留。"""
    raw = account.balance + account.monthly_reward
    new_balance = min(raw, BALANCE_CAP)
    wasted = raw - new_balance
    return RolloverResult(
        account=replace(account, balance=new_balance, monthly_reward=0),
        wasted=wasted,
    )


def consume(account: Account, amount) -> ConsumeResult:
    """按 当月奖励 -> 津贴 -> 余额 顺序扣减。

    合计不足时**整笔拒绝**，不做部分扣减、不产生负余额。
    """
    amount = _to_int(amount)
    if amount <= 0:
        raise ValueError("消费额度必须大于 0")
    if account.total < amount:
        return ConsumeResult(ok=False, account=account, amount=amount,
                             shortfall=amount - account.total)

    rest = amount
    changed = dict(account.__dict__)
    breakdown: dict[str, int] = {}
    for bucket in BUCKET_ORDER:
        have = changed[bucket]
        take = min(have, rest)
        if take <= 0:
            continue
        changed[bucket] = have - take
        breakdown[bucket] = take
        rest -= take
        if rest == 0:
            break
    return ConsumeResult(
        ok=True,
        account=replace(account, **changed),
        amount=amount,
        breakdown=breakdown,
    )


@dataclass(frozen=True)
class GrantLine:
    """一条发放明细（结算报告用）。"""

    qq_id: str
    character: str
    strat_pap: float
    reward: int
    reason: str = "ok"  # ok / below_threshold / no_qq / capped


def plan_grants(rows: Iterable[dict]) -> list[GrantLine]:
    """把 PAP 快照行转成发放明细（**纯计算，不落库**）。

    ``rows`` 每项需含 ``qq_id`` / ``character`` / ``strat_pap_count``。
    ``qq_id`` 为空的行标记 ``no_qq``（跳过，等待人工绑定）。
    """
    lines: list[GrantLine] = []
    for row in rows:
        qq = str(row.get("qq_id") or "").strip()
        name = str(row.get("character") or "").strip()
        pap = float(row.get("strat_pap_count") or 0)
        if not qq or qq.lower() in ("0", "none", "null"):
            lines.append(GrantLine(qq, name, pap, 0, "no_qq"))
            continue
        reward = reward_for_pap(pap)
        if reward <= 0:
            lines.append(GrantLine(qq, name, pap, 0, "below_threshold"))
        elif reward >= REWARD_MAX:
            lines.append(GrantLine(qq, name, pap, reward, "capped"))
        else:
            lines.append(GrantLine(qq, name, pap, reward, "ok"))
    return lines
