# -*- coding: utf-8 -*-
"""成员银行：LLM 工具与 Web 接口共用的业务入口。

所有函数第一个参数是 sqlite 连接，返回 dict（可直接序列化），
由 ``BankRuntime.run`` 在锁内、线程池中执行。
"""
from __future__ import annotations

import sqlite3

from . import db as bankdb
from . import parsing, service
from .settlement import Account


def fmt_isk(value: int) -> str:
    """把 ISK 整数格式化成玩家习惯的短写法（e=亿，b=十亿）。"""
    value = int(value or 0)
    sign = "-" if value < 0 else ""
    a = abs(value)
    if a >= 1_000_000_000:
        return f"{sign}{a / 1e9:.2f}b"
    if a >= 100_000_000:
        return f"{sign}{a / 1e8:.2f}e"
    if a >= 1_000_000:
        return f"{sign}{a / 1e6:.2f}m"
    if a >= 1_000:
        return f"{sign}{a / 1e3:.2f}k"
    return f"{sign}{a}"


def is_admin(qq: str, admins: set[str] | list[str] | None) -> bool:
    """管理员判定：以发送者 QQ 是否在白名单内为准（不看任何入参声明）。"""
    if not admins:
        return False
    return str(qq or "").strip() in {str(x).strip() for x in admins if str(x).strip()}


# ---------------- 成员侧 ----------------

def my_account(conn: sqlite3.Connection, qq_id: str) -> dict:
    """成员查自己的账户（不存在时给出可读提示，不自动建）。"""
    summary = service.member_summary(conn, str(qq_id))
    if summary is None:
        return {
            "ok": True,
            "exists": False,
            "message": "还没有账户。等第一次 PAP 结算达标后会自动建档。",
        }
    return {
        "ok": True,
        "exists": True,
        "character": summary["character"],
        "balance": summary["balance"],
        "balance_text": fmt_isk(summary["balance"]),
        "allowance": summary["allowance"],
        "allowance_text": fmt_isk(summary["allowance"]),
        "monthly_reward": summary["monthly_reward"],
        "monthly_reward_text": fmt_isk(summary["monthly_reward"]),
        "total": summary["total"],
        "total_text": fmt_isk(summary["total"]),
        "recent": [
            {
                "kind": r["kind"],
                "amount_text": fmt_isk(r["amount"]),
                "note": r["note"],
                "created_at": r["created_at"],
            }
            for r in summary["recent"]
        ],
    }


def place_order(
    conn: sqlite3.Connection, qq_id: str, character: str, item: str, quantity: int
) -> dict:
    """成员下单：只记物品与数量，金额等管理员确认。"""
    try:
        oid = service.create_order(
            conn, qq_id=str(qq_id), character=str(character or ""),
            item=item, quantity=int(quantity),
        )
    except ValueError as exc:
        return {"ok": False, "message": str(exc)}
    return {
        "ok": True,
        "order_id": oid,
        "message": f"已提交订单 #{oid}：{item} x{quantity}，等待管理员确认额度",
    }


def my_orders(conn: sqlite3.Connection, qq_id: str, limit: int = 10) -> dict:
    rows = bankdb.list_orders(conn, status=None, qq_id=str(qq_id), limit=int(limit))
    return {
        "ok": True,
        "count": len(rows),
        "orders": [
            {
                "id": r["id"],
                "item": r["item"],
                "quantity": r["quantity"],
                "status": r["status"],
                "amount_text": fmt_isk(r["amount"]) if r["amount"] is not None else "",
                "created_at": r["created_at"],
            }
            for r in rows
        ],
    }


# ---------------- 管理员侧 ----------------

def pending_orders(
    conn: sqlite3.Connection, status: str = "pending", limit: int = 50
) -> dict:
    rows = bankdb.list_orders(
        conn, status=(status or "pending"), limit=int(limit))
    return {
        "ok": True,
        "status": status or "pending",
        "count": len(rows),
        "orders": [
            {
                "id": r["id"],
                "qq_id": r["qq_id"],
                "character": r["character"],
                "item": r["item"],
                "quantity": r["quantity"],
                "status": r["status"],
                "amount_text": (
                    fmt_isk(r["amount"]) if r["amount"] is not None else ""),
                "created_at": r["created_at"],
            }
            for r in rows
        ],
    }


def confirm_order(
    conn: sqlite3.Connection, order_id: int, amount: int, operator: str
) -> dict:
    try:
        result = service.confirm_order(conn, int(order_id), int(amount), operator)
    except ValueError as exc:
        return {"ok": False, "message": str(exc)}
    if not result.get("ok"):
        return {
            "ok": False,
            "message": (
                f"余额不足，订单未扣款：总额 {fmt_isk(result['total'])}，"
                f"需 {fmt_isk(result['amount'])}，差 {fmt_isk(result['shortfall'])}"
            ),
        }
    acc = result["account"]
    return {
        "ok": True,
        "order_id": result["order_id"],
        "amount_text": fmt_isk(result["amount"]),
        "breakdown_text": {
            k: fmt_isk(v) for k, v in (result.get("breakdown") or {}).items()
        },
        "balance_text": fmt_isk(acc["balance"]),
        "allowance_text": fmt_isk(acc["allowance"]),
        "monthly_reward_text": fmt_isk(acc["monthly_reward"]),
        "total_text": fmt_isk(acc["total"]),
    }


def reject_order(
    conn: sqlite3.Connection, order_id: int, operator: str
) -> dict:
    try:
        ok = service.reject_order(conn, int(order_id), operator)
    except ValueError as exc:
        return {"ok": False, "message": str(exc)}
    return {
        "ok": ok,
        "message": f"订单 #{order_id} 已驳回" if ok else "订单不存在或已处理过",
    }


def accounts_table(conn: sqlite3.Connection, limit: int = 200) -> dict:
    """账户总览（Web 表格与管理员查询共用）。"""
    accounts = bankdb.list_accounts(conn)[: int(limit)]
    return {
        "ok": True,
        "count": len(accounts),
        "accounts": [
            {
                "qq_id": a.qq_id,
                "character": a.character,
                "balance": a.balance,
                "balance_text": fmt_isk(a.balance),
                "allowance": a.allowance,
                "allowance_text": fmt_isk(a.allowance),
                "monthly_reward": a.monthly_reward,
                "monthly_reward_text": fmt_isk(a.monthly_reward),
                "total": a.total,
                "total_text": fmt_isk(a.total),
                "status": a.status,
            }
            for a in accounts
        ],
    }


def set_allowance(
    conn: sqlite3.Connection, qq_id: str, amount: int, operator: str, period: str = ""
) -> dict:
    try:
        result = service.set_allowance(
            conn, str(qq_id), int(amount), operator, period=period)
    except ValueError as exc:
        return {"ok": False, "message": str(exc)}
    return {
        "ok": True,
        "allowance_text": fmt_isk(result["allowance"]),
        "balance_text": fmt_isk(result["balance"]),
        "message": f"已为 {qq_id} 发放津贴 {fmt_isk(amount)}",
    }


def preview_settlement(
    conn: sqlite3.Connection, period: str, rows: list[dict]
) -> dict:
    """结算预览（dry-run，不落库）。"""
    try:
        report = service.preview_settlement(conn, period, rows)
    except ValueError as exc:
        return {"ok": False, "message": str(exc)}
    return {
        "ok": True,
        "period": report.period,
        "granted_count": len(report.granted),
        "total_reward_text": fmt_isk(report.total_reward),
        "total_wasted_text": fmt_isk(report.total_wasted),
        "granted": [
            {
                "character": g["character"],
                "strat_pap": g["strat_pap"],
                "reward_text": fmt_isk(g["reward"]),
            }
            for g in report.granted
        ],
        "skipped_count": len(report.skipped),
    }


def apply_settlement(
    conn: sqlite3.Connection, period: str, rows: list[dict],
    operator: str, force: bool = False,
) -> dict:
    """执行结算（落库）。"""
    try:
        report = service.apply_settlement(
            conn, period, rows, operator=operator, force=force)
    except (ValueError, RuntimeError) as exc:
        return {"ok": False, "message": str(exc)}
    return {
        "ok": True,
        "period": report.period,
        "granted_count": len(report.granted),
        "total_reward_text": fmt_isk(report.total_reward),
        "total_wasted_text": fmt_isk(report.total_wasted),
        "carried_count": report.carried,
        "notes": report.notes,
    }


def adjust_balance(
    conn: sqlite3.Connection, qq_id: str, amount: int, operator: str,
    note: str = "人工调整",
) -> dict:
    try:
        result = service.adjust_balance(
            conn, str(qq_id), int(amount), operator, note=note)
    except ValueError as exc:
        return {"ok": False, "message": str(exc)}
    return {
        "ok": True,
        "balance_text": fmt_isk(result["balance"]),
        "total_text": fmt_isk(result["total"]),
        "message": f"已调整余额 {fmt_isk(amount)}，现为 {fmt_isk(result['balance'])}",
    }


# ---------------- 历史台账导入（Web） ----------------

def preview_import(conn: sqlite3.Connection, text: str) -> dict:
    """解析历史表格并给出压降/差异清单，不写库。"""
    rows = parsing.parse_import_table(text)
    if not rows:
        return {"ok": False, "message": "没有解析出任何数据行（需制表符分隔）"}
    plan = parsing.plan_import(rows)
    return {
        "ok": True,
        "row_count": len(rows),
        "accounts": [
            {
                "character": a["character"],
                "balance_text": fmt_isk(a["balance"]),
                "allowance_text": fmt_isk(a["allowance"]),
                "monthly_reward_text": fmt_isk(a["monthly_reward"]),
                "total_text": fmt_isk(
                    a["balance"] + a["allowance"] + a["monthly_reward"]),
            }
            for a in plan["accounts"]
        ],
        "capped": [
            {
                "character": c["character"],
                "declared_text": fmt_isk(c["declared"]),
                "applied_text": fmt_isk(c["applied"]),
                "wasted_text": fmt_isk(c["wasted"]),
            }
            for c in plan["capped"]
        ],
        "mismatched": [
            {
                "character": m["character"],
                "declared_text": fmt_isk(m["declared"]),
                "expected_text": fmt_isk(m["expected"]),
            }
            for m in plan["mismatched"]
        ],
        "total_balance_text": fmt_isk(plan["total_balance"]),
        "total_allowance_text": fmt_isk(plan["total_allowance"]),
        "total_wasted_text": fmt_isk(plan["total_wasted"]),
    }


def apply_import(
    conn: sqlite3.Connection,
    text: str,
    qq_by_name: dict[str, str],
    operator: str,
    period: str = "",
) -> dict:
    """把历史台账写入账户（幂等：按 角色名+期间 生成流水幂等键）。"""
    rows = parsing.parse_import_table(text)
    if not rows:
        return {"ok": False, "message": "没有解析出任何数据行"}
    plan = parsing.plan_import(rows)
    raw_by_name = {r["character"]: r["balance_raw"] for r in rows}

    stats = {"active": 0, "unclaimed": 0, "written": 0, "wasted": 0}
    with bankdb.transaction(conn):
        for a in plan["accounts"]:
            name = a["character"]
            qq = str((qq_by_name or {}).get(name) or "").strip()
            if qq:
                qq_id, status = qq, "active"
                stats["active"] += 1
            else:
                qq_id, status = f"unclaimed:{name}", "unclaimed"
                stats["unclaimed"] += 1

            raw = int(raw_by_name.get(name, a["balance"]))
            waste = raw - a["balance"]
            acc = Account(
                qq_id=qq_id, character=name, balance=a["balance"],
                allowance=a["allowance"], monthly_reward=a["monthly_reward"],
                status=status,
            )
            bankdb.upsert_account(conn, acc)

            key = f"import:{period}:{name}"
            total_in = raw + a["allowance"] + a["monthly_reward"]
            if bankdb.append_ledger(
                conn, qq_id=qq_id, kind="import", amount=total_in,
                balance_after=acc.balance, allowance_after=acc.allowance,
                period=period, operator=operator, idempotency_key=key,
                note=f"历史快照导入（{name}）",
            ):
                stats["written"] += 1
            if waste and bankdb.append_ledger(
                conn, qq_id=qq_id, kind="wasted", amount=-waste,
                balance_after=acc.balance, allowance_after=acc.allowance,
                period=period, operator=operator,
                idempotency_key=f"{key}:wasted", note="超出 2b 上限，作废",
            ):
                stats["wasted"] += waste

    return {
        "ok": True,
        "active": stats["active"],
        "unclaimed": stats["unclaimed"],
        "new_ledger": stats["written"],
        "wasted_text": fmt_isk(stats["wasted"]),
        "message": (
            f"导入完成：active {stats['active']} / unclaimed {stats['unclaimed']}，"
            f"新增流水 {stats['written']} 条"
        ),
    }
