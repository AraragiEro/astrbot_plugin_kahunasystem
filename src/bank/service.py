# -*- coding: utf-8 -*-
"""成员银行：业务编排（结算、消费、订单）。

所有会动账的操作都在一个事务里完成，并写幂等键，保证重复执行不重复记账。
纯标准库 + 本地 ``db``/``settlement``，不依赖 AstrBot，便于单测。
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Iterable

from . import db as bankdb
from .settlement import (
    BALANCE_CAP,
    Account,
    consume,
    grant_reward,
    plan_grants,
    rollover,
)

PERIOD_FMT = "%Y-%m"

# meta 里记录「最后一次执行月末结转的结算月」。
# 作用是让 force 重算同一个月时不会把本期刚发的奖励又当成上期奖励结掉。
META_LAST_ROLLOVER = "last_rollover_period"


def normalize_period(period: str) -> str:
    """校验并归一化结算月，形如 ``2026-10``。"""
    text = str(period or "").strip()
    try:
        from datetime import datetime

        return datetime.strptime(text, PERIOD_FMT).strftime(PERIOD_FMT)
    except ValueError as exc:
        raise ValueError(f"结算月格式应为 YYYY-MM，收到：{period!r}") from exc


@dataclass
class SettlementReport:
    """一次结算（预览或落账）的结果。"""

    period: str
    applied: bool = False
    total_reward: int = 0
    total_wasted: int = 0
    granted: list[dict] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)
    carried: int = 0
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "period": self.period,
            "applied": self.applied,
            "total_reward": self.total_reward,
            "total_wasted": self.total_wasted,
            "carried_count": self.carried,
            "granted": self.granted,
            "skipped": self.skipped,
            "notes": self.notes,
        }


# ---------------- 结算 ----------------

def _ensure_account(conn: sqlite3.Connection, qq_id: str, character: str) -> Account:
    """取账户，不存在则建（发钱范围是全部成员，PAP 达标即建档）。"""
    acc = bankdb.get_account(conn, qq_id)
    if acc is None:
        acc = Account(qq_id=qq_id, character=character, status="active")
    elif acc.character != character and character:
        # 角色名以最新快照为准（成员改名不应另起账户）
        acc = Account(qq_id=acc.qq_id, character=character, balance=acc.balance,
                      allowance=acc.allowance, monthly_reward=acc.monthly_reward,
                      status=acc.status)
    return acc


def preview_settlement(
    conn: sqlite3.Connection, period: str, rows: Iterable[dict]
) -> SettlementReport:
    """dry-run：只计算，不落库、不动账。"""
    period = normalize_period(period)
    rows = list(rows)
    report = SettlementReport(period=period, applied=False)

    run = bankdb.get_run(conn, period)
    if run and run["status"] == "applied":
        report.notes.append(f"{period} 已落账，本次为重复执行的预览")

    # 1) 先把各账户的 monthly_reward 结转进 balance（只模拟，不写库）
    carried = 0
    simulated: dict[str, Account] = {}
    for acc in bankdb.list_accounts(conn):
        result = rollover(acc)
        simulated[acc.qq_id] = result.account
        if acc.monthly_reward:
            carried += 1
        if result.wasted:
            report.total_wasted += result.wasted
    report.carried = carried

    # 2) 按 PAP 计算发放
    for line in plan_grants(rows):
        if line.reason == "no_qq":
            report.skipped.append({"character": line.character, "reason": "no_qq"})
            continue
        if line.reward <= 0:
            report.skipped.append({
                "qq_id": line.qq_id, "character": line.character,
                "strat_pap": line.strat_pap, "reason": line.reason,
            })
            continue
        acc = simulated.get(line.qq_id) or Account(
            qq_id=line.qq_id, character=line.character)
        new_acc = grant_reward(acc, line.reward)
        # 预览阶段不落库，只把结果塞回字典供后续行命中同一账户时累加
        simulated[line.qq_id] = new_acc
        report.total_reward += line.reward
        report.granted.append({
            "qq_id": line.qq_id,
            "character": line.character,
            "strat_pap": line.strat_pap,
            "reward": line.reward,
            "balance_before": acc.balance,
            "balance_after_cap": min(acc.balance + new_acc.monthly_reward,
                                     BALANCE_CAP),
            "reason": line.reason,
        })
    return report


def _rollover_locked(
    conn: sqlite3.Connection, period: str, operator: str, report: SettlementReport
) -> bool:
    """在当前事务内执行月末结转（monthly_reward 并入 balance 并封顶）。

    同一个结算月只执行一次：force 重算同一月时不会把本期刚发的奖励
    又当成上期奖励结掉。返回是否真的执行了结转。
    """
    if bankdb.get_meta(conn, META_LAST_ROLLOVER) == period:
        report.notes.append(f"{period} 已执行过月末结转，跳过")
        return False

    for acc in bankdb.list_accounts(conn):
        result = rollover(acc)
        if acc.monthly_reward:
            report.carried += 1
        if result.account == acc:
            continue
        bankdb.upsert_account(conn, result.account)
        if acc.monthly_reward:
            bankdb.append_ledger(
                conn,
                qq_id=acc.qq_id, kind="carry", amount=acc.monthly_reward,
                balance_after=result.account.balance,
                allowance_after=result.account.allowance,
                period=period, operator=operator,
                idempotency_key=f"carry:{period}:{acc.qq_id}",
                note="月末结转：当月奖励并入余额",
            )
        if result.wasted:
            # 作废额单独记一条负流水，账面可与人工核算对齐；
            # 只在真正写入时累加，避免重算把作废额报两次
            wrote = bankdb.append_ledger(
                conn,
                qq_id=acc.qq_id, kind="wasted", amount=-result.wasted,
                balance_after=result.account.balance,
                allowance_after=result.account.allowance,
                period=period, operator=operator,
                idempotency_key=f"wasted:{period}:{acc.qq_id}",
                note="超出 2b 上限，作废",
            )
            if wrote:
                report.total_wasted += result.wasted

    bankdb.set_meta(conn, META_LAST_ROLLOVER, period)
    return True


def apply_settlement(
    conn: sqlite3.Connection,
    period: str,
    rows: Iterable[dict],
    operator: str = "system",
    force: bool = False,
    do_rollover: bool = True,
) -> SettlementReport:
    """落账：结转上期奖励 -> 按 PAP 发放本期奖励。整批在一个事务里。"""
    period = normalize_period(period)
    rows = list(rows)
    report = SettlementReport(period=period, applied=True)

    with bankdb.transaction(conn):
        run = bankdb.get_run(conn, period)
        if run and run["status"] == "applied" and not force:
            raise RuntimeError(f"{period} 已落账，如需重算请显式 force")

        # 1) 结转（每个结算月只做一次）
        if do_rollover:
            _rollover_locked(conn, period, operator, report)

        # 2) 发放
        for line in plan_grants(rows):
            if line.reason == "no_qq":
                report.skipped.append({"character": line.character, "reason": "no_qq"})
                continue
            if line.reward <= 0:
                report.skipped.append({
                    "qq_id": line.qq_id, "character": line.character,
                    "strat_pap": line.strat_pap, "reason": line.reason,
                })
                continue

            key = f"reward:{period}:{line.qq_id}"
            # 先判重再动账：force 重算时不能把已发过的奖励再加一次
            if bankdb.has_ledger_key(conn, key):
                report.granted.append({
                    "qq_id": line.qq_id,
                    "character": line.character,
                    "strat_pap": line.strat_pap,
                    "reward": line.reward,
                    "reason": line.reason,
                    "already": True,
                })
                continue

            acc = _ensure_account(conn, line.qq_id, line.character)
            new_acc = grant_reward(acc, line.reward)
            bankdb.upsert_account(conn, new_acc)
            bankdb.append_ledger(
                conn,
                qq_id=line.qq_id, kind="reward", amount=line.reward,
                pap=line.strat_pap,
                balance_after=new_acc.balance,
                allowance_after=new_acc.allowance,
                period=period, operator=operator,
                idempotency_key=key,
                note=f"{period} Start PAP {line.strat_pap:g} → {line.reward // 100_000_000}e",
            )
            report.total_reward += line.reward
            report.granted.append({
                "qq_id": line.qq_id,
                "character": line.character,
                "strat_pap": line.strat_pap,
                "reward": line.reward,
                "reason": line.reason,
                "already": False,
            })

        bankdb.save_run(
            conn, period,
            status="applied",
            total_reward=report.total_reward,
            total_wasted=report.total_wasted,
            member_count=len(report.granted),
            operator=operator,
            detail=json.dumps(report.as_dict(), ensure_ascii=False),
        )
    return report


# ---------------- 消费 / 订单 ----------------

def create_order(
    conn: sqlite3.Connection,
    *,
    qq_id: str,
    character: str,
    item: str,
    quantity: int,
) -> int:
    """成员下单：只记「物品 + 数量」，金额等管理员确认。"""
    item = str(item or "").strip()
    if not item:
        raise ValueError("物品不能为空")
    if int(quantity) <= 0:
        raise ValueError("数量必须大于 0")
    with bankdb.transaction(conn):
        oid = bankdb.create_order(
            conn, qq_id=str(qq_id), character=str(character or ""),
            item=item, quantity=int(quantity),
        )
    return oid


def confirm_order(
    conn: sqlite3.Connection,
    order_id: int,
    amount: int,
    operator: str,
    *,
    period: str = "",
) -> dict:
    """管理员确认订单并扣款。扣减与订单状态在同一事务内完成。"""
    amount = int(amount)
    if amount <= 0:
        raise ValueError("消费额度必须大于 0")

    with bankdb.transaction(conn):
        order = bankdb.get_order(conn, order_id)
        if order is None:
            raise ValueError(f"订单不存在：{order_id}")
        if order["status"] != "pending":
            raise ValueError(f"订单已是 {order['status']}，不能重复确认")

        acc = bankdb.get_account(conn, order["qq_id"])
        if acc is None:
            raise ValueError("该成员没有账户，无法扣款")

        got = consume(acc, amount)
        if not got.ok:
            # 扣不动就整体拒绝：不写流水、不改订单
            return {
                "ok": False,
                "reason": "insufficient",
                "shortfall": got.shortfall,
                "total": acc.total,
                "amount": amount,
            }

        new_acc = got.account
        if order["character"] and new_acc.character != order["character"]:
            new_acc = Account(
                qq_id=new_acc.qq_id, character=order["character"],
                balance=new_acc.balance, allowance=new_acc.allowance,
                monthly_reward=new_acc.monthly_reward, status=new_acc.status,
            )
        bankdb.upsert_account(conn, new_acc)
        bankdb.set_order_status(conn, order_id, "confirmed",
                                amount=amount, operator=operator)
        bankdb.append_ledger(
            conn,
            qq_id=order["qq_id"], kind="consume", amount=-amount,
            balance_after=new_acc.balance,
            allowance_after=new_acc.allowance,
            period=period, order_id=order_id, operator=operator,
            idempotency_key=f"consume:{order_id}",
            note=f"{order['item']} x{order['quantity']}",
        )

    return {
        "ok": True,
        "order_id": order_id,
        "amount": amount,
        "breakdown": got.breakdown,
        "account": {
            "balance": new_acc.balance,
            "allowance": new_acc.allowance,
            "monthly_reward": new_acc.monthly_reward,
            "total": new_acc.total,
        },
    }


def reject_order(
    conn: sqlite3.Connection, order_id: int, operator: str, note: str = ""
) -> bool:
    """管理员驳回订单（不动账）。"""
    with bankdb.transaction(conn):
        return bankdb.set_order_status(
            conn, order_id, "rejected", operator=operator
        )


def member_summary(conn: sqlite3.Connection, qq_id: str) -> dict | None:
    """成员自查：账户 + 最近流水。"""
    acc = bankdb.get_account(conn, qq_id)
    if acc is None:
        return None
    ledgers = [dict(r) for r in bankdb.list_ledger(conn, qq_id=qq_id, limit=10)]
    return {
        "qq_id": acc.qq_id,
        "character": acc.character,
        "balance": acc.balance,
        "allowance": acc.allowance,
        "monthly_reward": acc.monthly_reward,
        "total": acc.total,
        "status": acc.status,
        "recent": ledgers,
    }


def adjust_balance(
    conn: sqlite3.Connection,
    qq_id: str,
    amount: int,
    operator: str,
    *,
    note: str = "人工调整",
) -> dict:
    """人工调整余额（正数加、负数减）。

    受两条硬约束：结果不能为负、不能超过 2b。津贴桶不受影响。
    """
    amount = int(amount)
    if amount == 0:
        raise ValueError("调整额度不能为 0")
    with bankdb.transaction(conn):
        acc = bankdb.get_account(conn, qq_id)
        if acc is None:
            raise ValueError("该成员没有账户")
        new_balance = acc.balance + amount
        if new_balance < 0:
            raise ValueError(f"调整后余额为负（{new_balance}），已拒绝")
        if new_balance > BALANCE_CAP:
            raise ValueError(
                f"调整后余额 {new_balance} 超过上限 {BALANCE_CAP}，已拒绝"
            )
        new_acc = Account(
            qq_id=acc.qq_id, character=acc.character, balance=new_balance,
            allowance=acc.allowance, monthly_reward=acc.monthly_reward,
            status=acc.status,
        )
        bankdb.upsert_account(conn, new_acc)
        bankdb.append_ledger(
            conn, qq_id=qq_id, kind="adjust", amount=amount,
            balance_after=new_acc.balance, allowance_after=new_acc.allowance,
            operator=operator, note=note,
        )
    return {"ok": True, "balance": new_acc.balance, "total": new_acc.total}


def set_allowance(
    conn: sqlite3.Connection,
    qq_id: str,
    amount: int,
    operator: str,
    *,
    period: str = "",
    note: str = "额外津贴",
) -> dict:
    """发放额外津贴（独立桶，不进 2b 封顶，永不清零）。"""
    amount = int(amount)
    if amount <= 0:
        raise ValueError("津贴额度必须大于 0")
    with bankdb.transaction(conn):
        acc = bankdb.get_account(conn, qq_id)
        if acc is None:
            acc = Account(qq_id=str(qq_id), status="active")
        new_acc = Account(
            qq_id=acc.qq_id, character=acc.character, balance=acc.balance,
            allowance=acc.allowance + amount, monthly_reward=acc.monthly_reward,
            status=acc.status,
        )
        bankdb.upsert_account(conn, new_acc)
        key = f"allowance:{period}:{qq_id}:{amount}" if period else ""
        bankdb.append_ledger(
            conn, qq_id=qq_id, kind="allowance", amount=amount,
            balance_after=new_acc.balance, allowance_after=new_acc.allowance,
            period=period, operator=operator, idempotency_key=key,
            note=note,
        )
    return {
        "ok": True,
        "allowance": new_acc.allowance,
        "balance": new_acc.balance,
        "quarter_total": new_acc.total,
    }
