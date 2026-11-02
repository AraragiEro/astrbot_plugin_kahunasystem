# -*- coding: utf-8 -*-
"""成员银行账本存储层（SQLite）。

纯标准库实现，不依赖 AstrBot，方便本地单测。
所有业务事务由调用方通过 ``transaction()`` 包裹，本模块只提供原子操作。
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

from .settlement import Account

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS member_account (
    qq_id          TEXT PRIMARY KEY,
    character      TEXT    NOT NULL DEFAULT '',
    balance        INTEGER NOT NULL DEFAULT 0,
    allowance      INTEGER NOT NULL DEFAULT 0,
    monthly_reward INTEGER NOT NULL DEFAULT 0,
    status         TEXT    NOT NULL DEFAULT 'active',
    created_at     TEXT    NOT NULL,
    updated_at     TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS pap_snapshot (
    period          TEXT NOT NULL,
    qq_id           TEXT NOT NULL DEFAULT '',
    character       TEXT NOT NULL DEFAULT '',
    pap_count       REAL NOT NULL DEFAULT 0,
    strat_pap_count REAL NOT NULL DEFAULT 0,
    source          TEXT NOT NULL DEFAULT 'seat',
    fetched_at      TEXT NOT NULL,
    PRIMARY KEY (period, character)
);

CREATE TABLE IF NOT EXISTS ledger (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    qq_id           TEXT    NOT NULL,
    period          TEXT    NOT NULL DEFAULT '',
    kind            TEXT    NOT NULL,
    amount          INTEGER NOT NULL,
    balance_after   INTEGER NOT NULL,
    allowance_after INTEGER NOT NULL,
    order_id        INTEGER,
    operator        TEXT    NOT NULL DEFAULT 'system',
    idempotency_key TEXT    NOT NULL UNIQUE,
    note            TEXT    NOT NULL DEFAULT '',
    created_at      TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ledger_qq ON ledger(qq_id, created_at);
CREATE INDEX IF NOT EXISTS idx_ledger_period ON ledger(period, kind);

CREATE TABLE IF NOT EXISTS member_order (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    qq_id        TEXT    NOT NULL,
    character    TEXT    NOT NULL DEFAULT '',
    item         TEXT    NOT NULL,
    quantity     INTEGER NOT NULL,
    status       TEXT    NOT NULL DEFAULT 'pending',
    amount       INTEGER,
    confirmed_by TEXT,
    confirmed_at TEXT,
    note         TEXT    NOT NULL DEFAULT '',
    created_at   TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_order_status ON member_order(status, created_at);

CREATE TABLE IF NOT EXISTS settlement_run (
    period        TEXT PRIMARY KEY,
    status        TEXT    NOT NULL,
    total_reward  INTEGER NOT NULL DEFAULT 0,
    total_wasted  INTEGER NOT NULL DEFAULT 0,
    member_count  INTEGER NOT NULL DEFAULT 0,
    operator      TEXT    NOT NULL DEFAULT 'system',
    detail        TEXT    NOT NULL DEFAULT '',
    created_at    TEXT    NOT NULL,
    updated_at    TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

ORDER_STATUSES = ("pending", "confirmed", "rejected")


def now_iso() -> str:
    """UTC ISO8601（秒精度，与 SeAT 抓取时间同口径）。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(db_path: str | Path) -> sqlite3.Connection:
    """打开（必要时创建）账本库。WAL + 外键，行工厂用 ``sqlite3.Row``。

    ``check_same_thread=False`` 是必需的：``BankRuntime.run`` 用
    ``asyncio.to_thread`` 把同步 sqlite 调用丢到线程池，连接在别的线程创建、
    在另一个线程使用。跨线程安全由 runtime 的那把 ``asyncio.Lock`` 保证
    （同一时刻只有一个操作在碰连接），这里只是解除 sqlite 的线程断言。
    """
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=15.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """建表（幂等）。"""
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(SCHEMA_VERSION),),
    )
    conn.commit()


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """事务上下文：异常整体回滚。"""
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def get_meta(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )


# ---------------- 账户 ----------------

def _row_to_account(row: sqlite3.Row) -> Account:
    return Account(
        qq_id=row["qq_id"],
        character=row["character"],
        balance=int(row["balance"]),
        allowance=int(row["allowance"]),
        monthly_reward=int(row["monthly_reward"]),
        status=row["status"],
    )


def get_account(conn: sqlite3.Connection, qq_id: str) -> Account | None:
    row = conn.execute(
        "SELECT * FROM member_account WHERE qq_id = ?", (str(qq_id),)
    ).fetchone()
    return _row_to_account(row) if row else None


def get_account_by_character(conn: sqlite3.Connection, character: str) -> Account | None:
    row = conn.execute(
        "SELECT * FROM member_account WHERE character = ? LIMIT 1", (character,)
    ).fetchone()
    return _row_to_account(row) if row else None


def list_accounts(conn: sqlite3.Connection) -> list[Account]:
    rows = conn.execute(
        "SELECT * FROM member_account ORDER BY balance DESC, qq_id"
    ).fetchall()
    return [_row_to_account(r) for r in rows]


def upsert_account(conn: sqlite3.Connection, account: Account) -> None:
    """写入账户（存在则更新三个桶与角色名）。"""
    ts = now_iso()
    conn.execute(
        """
        INSERT INTO member_account
            (qq_id, character, balance, allowance, monthly_reward, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(qq_id) DO UPDATE SET
            character      = excluded.character,
            balance        = excluded.balance,
            allowance      = excluded.allowance,
            monthly_reward = excluded.monthly_reward,
            status         = excluded.status,
            updated_at     = excluded.updated_at
        """,
        (
            account.qq_id,
            account.character,
            account.balance,
            account.allowance,
            account.monthly_reward,
            account.status,
            ts,
            ts,
        ),
    )


# ---------------- 流水 ----------------

def append_ledger(
    conn: sqlite3.Connection,
    *,
    qq_id: str,
    kind: str,
    amount: int,
    balance_after: int,
    allowance_after: int,
    period: str = "",
    order_id: int | None = None,
    operator: str = "system",
    idempotency_key: str = "",
    note: str = "",
) -> bool:
    """追加一条流水。幂等键冲突时返回 ``False``（不重复记账）。"""
    try:
        conn.execute(
            """
            INSERT INTO ledger
                (qq_id, period, kind, amount, balance_after, allowance_after,
                 order_id, operator, idempotency_key, note, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(qq_id), period, kind, int(amount), int(balance_after),
                int(allowance_after), order_id, operator, idempotency_key, note,
                now_iso(),
            ),
        )
        return True
    except sqlite3.IntegrityError:
        return False


def has_ledger_key(conn: sqlite3.Connection, idempotency_key: str) -> bool:
    """幂等键是否已记账（用于「先判重、再动账」的顺序）。"""
    if not idempotency_key:
        return False
    row = conn.execute(
        "SELECT 1 FROM ledger WHERE idempotency_key = ? LIMIT 1",
        (idempotency_key,),
    ).fetchone()
    return row is not None


def list_ledger(
    conn: sqlite3.Connection,
    qq_id: str | None = None,
    period: str | None = None,
    limit: int = 200,
) -> list[sqlite3.Row]:
    sql = "SELECT * FROM ledger WHERE 1=1"
    args: list = []
    if qq_id:
        sql += " AND qq_id = ?"
        args.append(str(qq_id))
    if period:
        sql += " AND period = ?"
        args.append(period)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(int(limit))
    return conn.execute(sql, args).fetchall()


def list_ledger_detail(
    conn: sqlite3.Connection,
    qq_id: str | None = None,
    period: str | None = None,
    kind: str | None = None,
    keyword: str | None = None,
    limit: int = 200,
) -> list[sqlite3.Row]:
    """流水浏览：左连账户表带出角色名，可按成员/期间/类型/关键字筛选。"""
    sql = (
        "SELECT l.*, COALESCE(a.character, '') AS character "
        "FROM ledger l LEFT JOIN member_account a ON a.qq_id = l.qq_id "
        "WHERE 1=1"
    )
    args: list = []
    if qq_id:
        sql += " AND l.qq_id = ?"
        args.append(str(qq_id))
    if period:
        sql += " AND l.period = ?"
        args.append(period)
    if kind:
        sql += " AND l.kind = ?"
        args.append(kind)
    if keyword:
        text = f"%{keyword}%"
        sql += " AND (l.qq_id LIKE ? OR a.character LIKE ? OR l.note LIKE ?)"
        args.extend([text, text, text])
    sql += " ORDER BY l.id DESC LIMIT ?"
    args.append(int(limit))
    return conn.execute(sql, args).fetchall()


def sum_ledger_by_period(
    conn: sqlite3.Connection, period: str, kind: str | None = None
) -> dict:
    """统计某期间已记账的条数与金额合计（结算预览用来看是否已经发过）。"""
    sql = "SELECT COUNT(*) AS c, COALESCE(SUM(amount), 0) AS s FROM ledger WHERE period = ?"
    args: list = [period]
    if kind:
        sql += " AND kind = ?"
        args.append(kind)
    row = conn.execute(sql, args).fetchone()
    return {"count": int(row["c"]), "amount": int(row["s"])}


def recalculate_balance(conn: sqlite3.Connection, qq_id: str) -> dict:
    """从流水重算账户余额，用于对账。

    返回 ``{"ledger_sum": 流水合计, "account_total": 账户三桶合计, "match": bool}``。
    流水里 ``carry`` 类记录的是结转损失（负值），其余为实际增减。
    """
    row = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) AS s FROM ledger WHERE qq_id = ?",
        (str(qq_id),),
    ).fetchone()
    ledger_sum = int(row["s"])
    acc = get_account(conn, qq_id)
    account_total = acc.total if acc else 0
    return {
        "ledger_sum": ledger_sum,
        "account_total": account_total,
        "match": ledger_sum == account_total,
    }


# ---------------- PAP 快照 ----------------

def save_snapshot(
    conn: sqlite3.Connection,
    period: str,
    rows: Iterable[dict],
    source: str = "seat",
    fetched_at: str | None = None,
) -> int:
    """保存某月的 PAP 快照（按 ``(period, character)`` 覆盖写入）。"""
    ts = fetched_at or now_iso()
    count = 0
    for row in rows:
        conn.execute(
            """
            INSERT INTO pap_snapshot
                (period, qq_id, character, pap_count, strat_pap_count, source, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(period, character) DO UPDATE SET
                qq_id           = excluded.qq_id,
                pap_count       = excluded.pap_count,
                strat_pap_count = excluded.strat_pap_count,
                source          = excluded.source,
                fetched_at      = excluded.fetched_at
            """,
            (
                period,
                str(row.get("qq_id") or "").strip(),
                str(row.get("character") or "").strip(),
                float(row.get("pap_count") or 0),
                float(row.get("strat_pap_count") or 0),
                source,
                ts,
            ),
        )
        count += 1
    return count


def list_snapshot(conn: sqlite3.Connection, period: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM pap_snapshot WHERE period = ? ORDER BY strat_pap_count DESC",
        (period,),
    ).fetchall()


def list_snapshot_periods(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT period FROM pap_snapshot ORDER BY period DESC"
    ).fetchall()
    return [r["period"] for r in rows]


# ---------------- 订单 ----------------

def create_order(
    conn: sqlite3.Connection,
    *,
    qq_id: str,
    character: str,
    item: str,
    quantity: int,
    note: str = "",
) -> int:
    cur = conn.execute(
        """
        INSERT INTO member_order
            (qq_id, character, item, quantity, status, note, created_at)
        VALUES (?, ?, ?, ?, 'pending', ?, ?)
        """,
        (str(qq_id), character, item, int(quantity), note, now_iso()),
    )
    return int(cur.lastrowid)


def get_order(conn: sqlite3.Connection, order_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM member_order WHERE id = ?", (int(order_id),)
    ).fetchone()


def list_orders(
    conn: sqlite3.Connection,
    status: str | None = "pending",
    qq_id: str | None = None,
    limit: int = 100,
) -> list[sqlite3.Row]:
    sql = "SELECT * FROM member_order WHERE 1=1"
    args: list = []
    if status:
        sql += " AND status = ?"
        args.append(status)
    if qq_id:
        sql += " AND qq_id = ?"
        args.append(str(qq_id))
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(int(limit))
    return conn.execute(sql, args).fetchall()


def set_order_status(
    conn: sqlite3.Connection,
    order_id: int,
    status: str,
    *,
    amount: int | None = None,
    operator: str = "system",
) -> bool:
    """更新订单状态（只允许 pending -> confirmed / rejected）。"""
    if status not in ORDER_STATUSES:
        raise ValueError(f"非法订单状态: {status}")
    cur = conn.execute(
        """
        UPDATE member_order
           SET status = ?, amount = COALESCE(?, amount),
               confirmed_by = ?, confirmed_at = ?
         WHERE id = ? AND status = 'pending'
        """,
        (status, amount, operator, now_iso(), int(order_id)),
    )
    return cur.rowcount == 1


# ---------------- 结算批次 ----------------

def get_run(conn: sqlite3.Connection, period: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM settlement_run WHERE period = ?", (period,)
    ).fetchone()


def save_run(
    conn: sqlite3.Connection,
    period: str,
    *,
    status: str,
    total_reward: int = 0,
    total_wasted: int = 0,
    member_count: int = 0,
    operator: str = "system",
    detail: str = "",
) -> None:
    ts = now_iso()
    conn.execute(
        """
        INSERT INTO settlement_run
            (period, status, total_reward, total_wasted, member_count,
             operator, detail, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(period) DO UPDATE SET
            status       = excluded.status,
            total_reward = excluded.total_reward,
            total_wasted = excluded.total_wasted,
            member_count = excluded.member_count,
            operator     = excluded.operator,
            detail       = excluded.detail,
            updated_at   = excluded.updated_at
        """,
        (
            period, status, int(total_reward), int(total_wasted),
            int(member_count), operator, detail, ts, ts,
        ),
    )
