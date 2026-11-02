# -*- coding: utf-8 -*-
"""结算引擎与账本存储的单元测试（纯标准库 unittest，避免外部依赖）。

跑法（插件仓库根目录）：
    python -m unittest tests.test_bank_settlement -v
"""
import os
import sqlite3
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.bank import db as bankdb  # noqa: E402
from src.bank.settlement import (  # noqa: E402
    BALANCE_CAP,
    REWARD_MAX,
    Account,
    consume,
    grant_reward,
    plan_grants,
    reward_for_pap,
    rollover,
)

E = 100_000_000
B = 1_000_000_000


class RewardTierTests(unittest.TestCase):
    CASES = [
        (0, 0),
        (2.5, 0),
        (5, 0),
        (5.99, 0),        # 向下取整 -> 5，不发
        (6, 2 * E),       # 起步 2e
        (8, 2 * E),
        (9, 3 * E),
        (11, 3 * E),
        (12, 4 * E),
        (14, 4 * E),
        (15, 5 * E),      # 封顶
        (16, 5 * E),
        (107.5, 5 * E),   # 实测最大值，向下取整 107
        (9.5, 3 * E),     # 向下取整 9 -> 3e
        (None, 0),
        ("", 0),
        ("9.50", 3 * E),
        ("107.50", REWARD_MAX),
    ]

    def test_tiers(self):
        for pap, expected in self.CASES:
            with self.subTest(pap=pap):
                self.assertEqual(reward_for_pap(pap), expected)

    def test_garbage(self):
        self.assertEqual(reward_for_pap("abc"), 0)
        self.assertEqual(reward_for_pap(float("nan")), 0)


class RolloverTests(unittest.TestCase):
    def test_merges_and_caps(self):
        acc = Account(qq_id="1", balance=1_900_000_000, monthly_reward=300_000_000)
        result = rollover(acc)
        self.assertEqual(result.account.balance, BALANCE_CAP)
        self.assertEqual(result.account.monthly_reward, 0)
        self.assertEqual(result.wasted, 200_000_000)

    def test_allowance_survives(self):
        acc = Account(qq_id="1", balance=B, allowance=300_000_000)
        result = rollover(acc)
        self.assertEqual(result.account.balance, B)
        self.assertEqual(result.account.allowance, 300_000_000)
        self.assertEqual(result.wasted, 0)

    def test_no_waste_under_cap(self):
        acc = Account(qq_id="1", balance=B, monthly_reward=500_000_000)
        result = rollover(acc)
        self.assertEqual(result.account.balance, 1_500_000_000)
        self.assertEqual(result.wasted, 0)

    def test_clamps_legacy_over_cap(self):
        acc = Account(qq_id="1", balance=3 * B)
        result = rollover(acc)
        self.assertEqual(result.account.balance, BALANCE_CAP)
        self.assertEqual(result.wasted, B)


class ConsumeTests(unittest.TestCase):
    def test_three_bucket_order(self):
        acc = Account(qq_id="1", monthly_reward=200_000_000,
                      allowance=300_000_000, balance=1_000_000_000)
        got = consume(acc, 600_000_000)
        self.assertTrue(got.ok)
        self.assertEqual(got.account.monthly_reward, 0)
        self.assertEqual(got.account.allowance, 0)
        self.assertEqual(got.account.balance, 900_000_000)
        self.assertEqual(got.breakdown, {
            "monthly_reward": 200_000_000,
            "allowance": 300_000_000,
            "balance": 100_000_000,
        })

    def test_partial_bucket(self):
        acc = Account(qq_id="1", monthly_reward=E, balance=50_000_000)
        got = consume(acc, 120_000_000)
        self.assertTrue(got.ok)
        self.assertEqual(got.account.monthly_reward, 0)
        self.assertEqual(got.account.balance, 30_000_000)
        self.assertEqual(got.breakdown, {"monthly_reward": E, "balance": 20_000_000})

    def test_exact_total(self):
        got = consume(Account(qq_id="1", monthly_reward=E), E)
        self.assertTrue(got.ok)
        self.assertEqual(got.account.total, 0)

    def test_rejects_overdraft(self):
        got = consume(Account(qq_id="1", monthly_reward=E), E + 1)
        self.assertFalse(got.ok)
        self.assertEqual(got.shortfall, 1)
        self.assertEqual(got.account.total, E)  # 拒绝时不扣任何一分

    def test_requires_positive(self):
        for bad in (0, -5):
            with self.assertRaises(ValueError):
                consume(Account(qq_id="1", balance=B), bad)


class PlanGrantTests(unittest.TestCase):
    def test_marks_reasons(self):
        rows = [
            {"qq_id": "1", "character": "A", "strat_pap_count": 18},
            {"qq_id": "2", "character": "B", "strat_pap_count": 3},
            {"qq_id": "", "character": "C", "strat_pap_count": 30},
            {"qq_id": "4", "character": "D", "strat_pap_count": 9.5},
        ]
        lines = plan_grants(rows)
        self.assertEqual([x.reason for x in lines],
                         ["capped", "below_threshold", "no_qq", "ok"])
        self.assertEqual(lines[0].reward, REWARD_MAX)
        self.assertEqual(lines[1].reward, 0)
        self.assertEqual(lines[2].reward, 0)
        self.assertEqual(lines[3].reward, 3 * E)

    def test_grant_accumulates(self):
        acc = grant_reward(Account(qq_id="1", balance=B), 2 * E)
        acc = grant_reward(acc, 3 * E)
        self.assertEqual(acc.monthly_reward, 5 * E)
        self.assertEqual(acc.balance, B)  # 发放不直接改余额


class StorageTests(unittest.TestCase):
    def setUp(self):
        # 用内存库：不碰文件系统（Windows 沙箱下子进程 mkdir 会被 ACL 拒绝），
        # 也让每个用例天然隔离。
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        bankdb.init_schema(self.conn)

    def tearDown(self):
        try:
            self.conn.close()
        except Exception:
            pass

    def test_account_roundtrip(self):
        acc = Account(qq_id="10001", character="Tester", balance=B, allowance=E)
        bankdb.upsert_account(self.conn, acc)
        self.assertEqual(bankdb.get_account(self.conn, "10001"), acc)
        self.assertEqual(bankdb.get_account_by_character(self.conn, "Tester"), acc)

    def test_ledger_idempotent(self):
        bankdb.upsert_account(self.conn, Account(qq_id="1"))
        kwargs = dict(qq_id="1", kind="reward", amount=E, balance_after=E,
                      allowance_after=0, period="2026-10",
                      idempotency_key="reward:2026-10:1")
        self.assertTrue(bankdb.append_ledger(self.conn, **kwargs))
        self.assertFalse(bankdb.append_ledger(self.conn, **kwargs))
        self.assertEqual(len(bankdb.list_ledger(self.conn, qq_id="1")), 1)

    def test_recalculate_matches(self):
        bankdb.upsert_account(self.conn, Account(qq_id="1", balance=5 * B))
        bankdb.append_ledger(self.conn, qq_id="1", kind="reward", amount=5 * B,
                             balance_after=5 * B, allowance_after=0)
        self.assertTrue(bankdb.recalculate_balance(self.conn, "1")["match"])

    def test_snapshot_upsert(self):
        rows = [
            {"qq_id": "1", "character": "A", "pap_count": 10, "strat_pap_count": 9.5},
            {"qq_id": "", "character": "B", "pap_count": 1, "strat_pap_count": 0},
        ]
        self.assertEqual(bankdb.save_snapshot(self.conn, "2026-10", rows), 2)
        # 同月同角色重复写入应覆盖而非新增
        bankdb.save_snapshot(self.conn, "2026-10",
                             [dict(rows[0], strat_pap_count=12.0)])
        got = bankdb.list_snapshot(self.conn, "2026-10")
        self.assertEqual(len(got), 2)
        a = [r for r in got if r["character"] == "A"][0]
        self.assertEqual(a["strat_pap_count"], 12.0)

    def test_order_lifecycle(self):
        oid = bankdb.create_order(self.conn, qq_id="1", character="A",
                                  item="三钛合金", quantity=100)
        self.assertEqual(bankdb.get_order(self.conn, oid)["status"], "pending")
        self.assertTrue(bankdb.set_order_status(
            self.conn, oid, "confirmed", amount=E, operator="admin"))
        row = bankdb.get_order(self.conn, oid)
        self.assertEqual(row["status"], "confirmed")
        self.assertEqual(row["amount"], E)
        # 已确认的订单不能再改
        self.assertFalse(bankdb.set_order_status(self.conn, oid, "rejected"))

    def test_init_schema_adds_pap_column_to_old_db(self):
        """线上老库没有 pap 列，init_schema 必须自动补上（增量迁移）。"""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE ledger (
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
            """
        )
        bankdb.init_schema(conn)
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(ledger)")}
        self.assertIn("pap", cols)
        conn.close()

    def test_settlement_run_upsert(self):
        bankdb.save_run(self.conn, "2026-10", status="preview", total_reward=E)
        self.assertEqual(bankdb.get_run(self.conn, "2026-10")["status"], "preview")
        bankdb.save_run(self.conn, "2026-10", status="applied", total_reward=2 * E)
        row = bankdb.get_run(self.conn, "2026-10")
        self.assertEqual(row["status"], "applied")
        self.assertEqual(row["total_reward"], 2 * E)


if __name__ == "__main__":
    unittest.main(verbosity=2)
