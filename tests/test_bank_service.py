# -*- coding: utf-8 -*-
"""业务编排层（结算 / 导入 / 订单）的集成测试。

跑法（插件仓库根目录）：
    python tests/test_bank_service.py
"""
import os
import sqlite3
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.bank import db as bankdb  # noqa: E402
from src.bank import handlers, parsing, service  # noqa: E402
from src.bank.settlement import BALANCE_CAP, Account  # noqa: E402

E = 100_000_000
B = 1_000_000_000

# 历史台账样例（制表符分隔；名字内含空格，必须按 tab 切列）。
# 金额与结构取自真实台账，**角色名已脱敏为 M01~M37**——这个仓库是对外可见的，
# 不能把成员身份与余额一起写进测试。
IMPORT_TEXT = "\n".join([
    "8月\t次月结余\t本月额度\t特别津贴\t结余",
    "M01\t1,500,000,000\t0.00\t\t1,500,000,000",
    "M02\t1,000,000,000\t0.00\t\t1,000,000,000",
    "M03\t700,000,000\t0.00\t\t700,000,000",
    "M04\t600,000,000\t0.00\t\t600,000,000",
    "M05\t500,000,000\t0.00\t\t500,000,000",
    "M06\t2,000,000,000\t0.00\t\t2,000,000,000",
    "M07\t2,000,000,000\t0.00\t\t2,000,000,000",
    "M08\t2,100,000,000\t0.00\t1,000,000,000\t3,000,000,000",
    "M09\t2,000,000,000\t0.00\t\t2,000,000,000",
    "M10\t3,000,000,000\t0.00\t1,000,000,000\t3,000,000,000",
    "M11\t2,000,000,000\t0.00\t\t2,000,000,000",
    "M12\t3,000,000,000\t0.00\t1,000,000,000\t3,000,000,000",
    "M13\t3,000,000,000\t0.00\t1,000,000,000\t3,000,000,000",
    "M14\t200,000,000\t0.00\t\t200,000,000",
    "M15\t2,000,000,000\t0.00\t\t2,000,000,000",
    "M16\t200,000,000\t0.00\t\t200,000,000",
    "M17\t1,000,000,000\t500,000,000.00\t\t1,500,000,000",
    "M18\t800,000,000\t0.00\t\t800,000,000",
    "M19\t2,000,000,000\t300,000,000.00\t\t2,000,000,000",
    "M20\t1,000,000,000\t0.00\t\t1,000,000,000",
    "M21\t2,000,000,000\t0.00\t\t2,000,000,000",
    "M22\t1,600,000,000\t0.00\t\t1,600,000,000",
    "M23\t1,400,000,000\t0.00\t\t1,400,000,000",
    "M24\t800,000,000\t0.00\t\t800,000,000",
    "M25\t1,500,000,000\t0.00\t\t1,500,000,000",
    "M26\t2,000,000,000\t0.00\t\t2,000,000,000",
    "M27\t1,500,000,000\t0.00\t\t1,500,000,000",
    "M28\t1,000,000,000\t0.00\t\t1,000,000,000",
    "M29\t1,800,000,000\t0.00\t\t1,800,000,000",
    "M30\t1,100,000,000\t0.00\t\t1,100,000,000",
    "M31\t1,100,000,000\t0.00\t\t1,100,000,000",
    "M32\t1,100,000,000\t0.00\t\t1,100,000,000",
    "M33\t600,000,000\t0.00\t\t600,000,000",
    "M34\t1,400,000,000\t0.00\t\t1,400,000,000",
    "M35\t1,000,000,000\t0.00\t\t1,000,000,000",
    "M36\t400,000,000\t0.00\t\t400,000,000",
    "M37\t400,000,000\t0.00\t\t400,000,000",
])


class ImportParsingTests(unittest.TestCase):
    def test_parses_all_rows(self):
        rows = parsing.parse_import_table(IMPORT_TEXT)
        self.assertEqual(len(rows), 37)
        names = [r["character"] for r in rows]
        self.assertEqual(names[0], "M01")
        self.assertEqual(names[-1], "M37")
        self.assertEqual(len(set(names)), 37)

    def test_character_name_with_spaces(self):
        """角色名内含空格时必须按 tab 切列，不能被拆成两列。"""
        rows = parsing.parse_import_table(
            "Has Space Name\t1,000,000\t0.00\t\t1,000,000")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["character"], "Has Space Name")
        self.assertEqual(rows[0]["balance_raw"], 1_000_000)

    def test_amounts(self):
        rows = {r["character"]: r for r in parsing.parse_import_table(IMPORT_TEXT)}
        self.assertEqual(rows["M01"]["balance_raw"], 1_500_000_000)
        self.assertEqual(rows["M08"]["allowance"], 1_000_000_000)
        self.assertEqual(rows["M17"]["monthly_reward"], 500_000_000)
        self.assertEqual(rows["M36"]["balance_raw"], 400_000_000)

    def test_plan_caps_over_2b(self):
        plan = parsing.plan_import(parsing.parse_import_table(IMPORT_TEXT))
        capped = {c["character"]: c for c in plan["capped"]}
        self.assertEqual(set(capped), {"M08", "M10", "M12", "M13"})
        self.assertEqual(capped["M08"]["wasted"], 100_000_000)
        self.assertEqual(capped["M10"]["wasted"], B)
        self.assertEqual(plan["total_wasted"], 3_100_000_000)

    def test_allowance_not_capped(self):
        plan = parsing.plan_import(parsing.parse_import_table(IMPORT_TEXT))
        acc = {a["character"]: a for a in plan["accounts"]}
        # 津贴独立：M10 余额压到 2b，但 1b 津贴原样保留
        self.assertEqual(acc["M10"]["balance"], BALANCE_CAP)
        self.assertEqual(acc["M10"]["allowance"], B)
        # 「本月额度」列是当月奖励，不是津贴
        self.assertEqual(acc["M19"]["allowance"], 0)
        self.assertEqual(acc["M19"]["monthly_reward"], 300_000_000)
        self.assertEqual(acc["M17"]["monthly_reward"], 500_000_000)
        # 只有「特别津贴」列计入津贴合计（4 人各 1b）
        self.assertEqual(plan["total_allowance"], 4 * B)

    def test_declared_total_mismatch_reported(self):
        """末列「结余」与「次月结余+额度+津贴」不一致的行必须报出来。"""
        plan = parsing.plan_import(parsing.parse_import_table(IMPORT_TEXT))
        bad = {m["character"] for m in plan["mismatched"]}
        self.assertEqual(bad, {"M08", "M10", "M12", "M13", "M19"})


class SettlementFlowTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        bankdb.init_schema(self.conn)

    def tearDown(self):
        self.conn.close()

    ROWS = [
        {"character": "Top", "qq_id": "1", "strat_pap_count": 18},     # 封顶 5e
        {"character": "Mid", "qq_id": "2", "strat_pap_count": 9.5},    # 3e
        {"character": "Low", "qq_id": "3", "strat_pap_count": 3},      # 不发
        {"character": "NoQQ", "qq_id": "", "strat_pap_count": 30},     # 跳过
    ]

    def test_preview_does_not_touch_db(self):
        report = service.preview_settlement(self.conn, "2026-10", self.ROWS)
        self.assertFalse(report.applied)
        self.assertEqual(report.total_reward, 8 * E)
        self.assertEqual(bankdb.list_accounts(self.conn), [])  # 预览不建账户

    def test_apply_then_idempotent(self):
        report = service.apply_settlement(self.conn, "2026-10", self.ROWS)
        self.assertTrue(report.applied)
        self.assertEqual(report.total_reward, 8 * E)
        self.assertEqual(len(bankdb.list_accounts(self.conn)), 2)

        acc = bankdb.get_account(self.conn, "2")
        self.assertEqual(acc.monthly_reward, 3 * E)
        self.assertEqual(acc.balance, 0)  # 奖励先进 monthly_reward 桶

        # 未 force 的重复结算必须拒绝
        with self.assertRaises(RuntimeError):
            service.apply_settlement(self.conn, "2026-10", self.ROWS)

        # force 重算不能重复发钱
        again = service.apply_settlement(self.conn, "2026-10", self.ROWS, force=True)
        self.assertEqual(again.total_reward, 0)
        self.assertEqual(bankdb.get_account(self.conn, "2").monthly_reward, 3 * E)

    def test_rollover_merges_into_balance_with_cap(self):
        bankdb.upsert_account(self.conn, Account(
            qq_id="9", character="Rich", balance=1_900_000_000,
            monthly_reward=300_000_000))
        report = service.apply_settlement(self.conn, "2026-10", [])
        acc = bankdb.get_account(self.conn, "9")
        self.assertEqual(acc.balance, BALANCE_CAP)
        self.assertEqual(acc.monthly_reward, 0)
        self.assertEqual(report.total_wasted, 200_000_000)

    def test_allowance_survives_settlement(self):
        bankdb.upsert_account(self.conn, Account(
            qq_id="8", character="Vip", balance=B, allowance=300_000_000))
        service.apply_settlement(self.conn, "2026-10", [])
        acc = bankdb.get_account(self.conn, "8")
        self.assertEqual(acc.allowance, 300_000_000)  # 永不清零
        self.assertEqual(acc.balance, B)              # 不被计入 2b 封顶

    def test_invalid_period(self):
        for bad in ("2026/10", "202610", "", "2026-13"):
            with self.assertRaises(ValueError):
                service.normalize_period(bad)


class OrderFlowTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        bankdb.init_schema(self.conn)
        bankdb.upsert_account(self.conn, Account(
            qq_id="1001", character="Buyer", balance=B,
            allowance=200_000_000, monthly_reward=E))

    def tearDown(self):
        self.conn.close()

    def test_order_confirm_deducts_in_bucket_order(self):
        oid = service.create_order(
            self.conn, qq_id="1001", character="Buyer",
            item="三钛合金", quantity=1000)
        # 账户三桶合计 1.3b；消费 1.1b -> 先扣光奖励与津贴，再从余额扣 8e
        got = service.confirm_order(self.conn, oid, 1_100_000_000, operator="admin")
        self.assertTrue(got["ok"])
        self.assertEqual(got["breakdown"], {
            "monthly_reward": E, "allowance": 200_000_000, "balance": 800_000_000,
        })
        acc = bankdb.get_account(self.conn, "1001")
        self.assertEqual(acc.monthly_reward, 0)
        self.assertEqual(acc.allowance, 0)
        self.assertEqual(acc.balance, 200_000_000)

        row = bankdb.get_order(self.conn, oid)
        self.assertEqual(row["status"], "confirmed")
        self.assertEqual(row["amount"], 1_100_000_000)
        led = bankdb.list_ledger(self.conn, qq_id="1001")
        self.assertEqual(led[0]["kind"], "consume")
        self.assertEqual(led[0]["amount"], -1_100_000_000)

    def test_overdraft_rejected_without_side_effects(self):
        oid = service.create_order(
            self.conn, qq_id="1001", character="Buyer", item="铁", quantity=1)
        # 三桶合计 1.3b，多花 1 ISK 就必须整笔拒绝
        got = service.confirm_order(self.conn, oid, 1_300_000_001, operator="admin")
        self.assertFalse(got["ok"])
        self.assertEqual(got["reason"], "insufficient")
        self.assertEqual(got["shortfall"], 1)
        # 订单仍是 pending，账上一分没动
        self.assertEqual(bankdb.get_order(self.conn, oid)["status"], "pending")
        acc = bankdb.get_account(self.conn, "1001")
        self.assertEqual(acc.total, B + 200_000_000 + E)
        self.assertEqual(bankdb.list_ledger(self.conn, qq_id="1001"), [])

    def test_reject_order_keeps_money(self):
        oid = service.create_order(
            self.conn, qq_id="1001", character="Buyer", item="铁", quantity=1)
        self.assertTrue(service.reject_order(self.conn, oid, operator="admin"))
        self.assertEqual(bankdb.get_order(self.conn, oid)["status"], "rejected")
        self.assertEqual(bankdb.list_ledger(self.conn, qq_id="1001"), [])

    def test_cannot_confirm_twice(self):
        oid = service.create_order(
            self.conn, qq_id="1001", character="Buyer", item="铁", quantity=1)
        service.confirm_order(self.conn, oid, E, operator="admin")
        with self.assertRaises(ValueError):
            service.confirm_order(self.conn, oid, E, operator="admin")

    def test_invalid_order_input(self):
        with self.assertRaises(ValueError):
            service.create_order(self.conn, qq_id="1001", character="B",
                                 item="  ", quantity=1)
        with self.assertRaises(ValueError):
            service.create_order(self.conn, qq_id="1001", character="B",
                                 item="铁", quantity=0)

    def test_member_summary(self):
        summary = service.member_summary(self.conn, "1001")
        self.assertIsNotNone(summary)
        self.assertEqual(summary["total"], B + 200_000_000 + E)
        self.assertIsNone(service.member_summary(self.conn, "nobody"))


class LedgerBrowseTests(unittest.TestCase):
    """流水浏览与结算预览的「是否已发放」标记。"""

    ROWS = [
        {"character": "Top", "qq_id": "1", "strat_pap_count": 18},
        {"character": "Mid", "qq_id": "2", "strat_pap_count": 9},
    ]

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        bankdb.init_schema(self.conn)
        bankdb.upsert_account(self.conn, Account(
            qq_id="1", character="Top", balance=E))
        bankdb.upsert_account(self.conn, Account(
            qq_id="2", character="Mid", balance=2 * E))
        bankdb.append_ledger(
            self.conn, qq_id="1", kind="reward", amount=E,
            balance_after=E, allowance_after=0, period="2026-10",
            operator="system", idempotency_key="reward:2026-10:1",
            note="发放")
        bankdb.append_ledger(
            self.conn, qq_id="1", kind="consume", amount=-E // 2,
            balance_after=E // 2, allowance_after=0, period="2026-10",
            operator="admin", idempotency_key="consume:99",
            note="三钛合金 x10", order_id=99)

    def tearDown(self):
        self.conn.close()

    def test_lists_newest_first_with_character(self):
        data = handlers.ledger_entries(self.conn)
        self.assertEqual(data["count"], 2)
        self.assertEqual(data["entries"][0]["kind"], "consume")  # id DESC
        self.assertEqual(data["entries"][0]["character"], "Top")
        self.assertEqual(data["entries"][0]["kind_label"], "消费扣款")
        self.assertEqual(data["entries"][0]["order_id"], 99)
        self.assertEqual(data["entries"][1]["kind_label"], "发放奖励")

    def test_filter_by_kind(self):
        data = handlers.ledger_entries(self.conn, kind="reward")
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["entries"][0]["qq_id"], "1")

    def test_filter_by_keyword_matches_character_and_note(self):
        self.assertEqual(handlers.ledger_entries(self.conn, keyword="Top")["count"], 2)
        self.assertEqual(handlers.ledger_entries(self.conn, keyword="三钛")["count"], 1)
        self.assertEqual(handlers.ledger_entries(self.conn, keyword="nobody")["count"], 0)

    def test_filter_by_period(self):
        self.assertEqual(handlers.ledger_entries(self.conn, period="2026-10")["count"], 2)
        self.assertEqual(handlers.ledger_entries(self.conn, period="2026-09")["count"], 0)

    def test_preview_reports_existing_rewards(self):
        # setUp 里已有一条 2026-10 的 reward（qq=1），预览必须如实报告
        before = handlers.preview_settlement(self.conn, "2026-10", self.ROWS)
        self.assertEqual(before["already_reward_count"], 1)
        self.assertEqual(before["period_ledger_count"], 2)

        # 再执行一次：qq=1 被幂等键跳过，只补发 qq=2
        service.apply_settlement(self.conn, "2026-10", self.ROWS)

        after = handlers.preview_settlement(self.conn, "2026-10", self.ROWS)
        self.assertEqual(after["already_reward_count"], 2)  # 原有 1 + 补发 1
        # qq=1 的发放流水没有被重复写入
        rewards = [
            r for r in bankdb.list_ledger(self.conn, qq_id="1", period="2026-10")
            if r["kind"] == "reward"
        ]
        self.assertEqual(len(rewards), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
