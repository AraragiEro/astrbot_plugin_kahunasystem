# -*- coding: utf-8 -*-
"""纯解析函数：SeAT PAP 响应归一化、历史台账表格解析。

不含网络与 AstrBot 依赖，可直接单测。
"""
from __future__ import annotations

import re

from .settlement import BALANCE_CAP

# SeAT 返回的 qq_id 可能出现的空值写法
_EMPTY_QQ = {"", "0", "none", "null", "-"}


def _to_float(value) -> float:
    try:
        return float(str(value).replace(",", "").strip() or 0)
    except (TypeError, ValueError):
        return 0.0


def _clean_qq(value) -> str:
    text = str(value if value is not None else "").strip()
    return "" if text.lower() in _EMPTY_QQ else text


def normalize_pap_rows(payload: dict) -> list[dict]:
    """把 paptracking 的 DataTables 响应归一化成行列表。

    字段：``character / nickname / qq_id / pap_count / strat_pap_count / logoff_date``
    """
    rows = []
    for item in (payload or {}).get("data") or []:
        if not isinstance(item, dict):
            continue
        rows.append({
            "character": str(item.get("character") or "").strip(),
            "nickname": str(item.get("nickname") or "").strip(),
            "qq_id": _clean_qq(item.get("qq_id")),
            "pap_count": _to_float(item.get("pap_count")),
            "strat_pap_count": _to_float(item.get("strat_pap_count")),
            "logoff_date": str(item.get("logoff_date") or "").strip(),
        })
    return rows


def _to_isk(value) -> int:
    """把 ``1,500,000,000`` / ``0.00`` / 空串解析成 ISK 整数。"""
    text = str(value if value is not None else "").strip().replace(",", "")
    if not text:
        return 0
    try:
        return int(round(float(text)))
    except ValueError:
        return 0


_HEADER_HINTS = ("次月结余", "本月额度", "特别津贴", "结余", "余额", "角色")


def _looks_like_header(cells: list[str]) -> bool:
    joined = " ".join(cells)
    # 表头里不会出现纯数字金额
    if re.fullmatch(r"[\d,.\s]+", joined.replace("\t", "")):
        return False
    return any(hint in joined for hint in _HEADER_HINTS)


def parse_import_table(text: str) -> list[dict]:
    """解析历史台账（制表符分隔，首行可含表头）。

    列序：``角色名 / 次月结余 / 本月额度 / 特别津贴 / 结余``，
    角色名内含空格（如 ``Wynn Tim``），因此**必须按 tab 切列**。
    """
    rows: list[dict] = []
    for raw in str(text or "").splitlines():
        line = raw.rstrip("\r")
        if not line.strip():
            continue
        cells = [c.strip() for c in line.split("\t")]
        if _looks_like_header(cells):
            continue
        if len(cells) < 2:
            continue
        name = cells[0].strip()
        if not name:
            continue
        balance = _to_isk(cells[1] if len(cells) > 1 else 0)
        monthly = _to_isk(cells[2] if len(cells) > 2 else 0)
        allowance = _to_isk(cells[3] if len(cells) > 3 else 0)
        declared = _to_isk(cells[4] if len(cells) > 4 else 0)
        rows.append({
            "character": name,
            "balance_raw": balance,
            "monthly_reward": monthly,
            "allowance": allowance,
            "total_declared": declared,
        })
    return rows


def plan_import(rows: list[dict]) -> dict:
    """把历史行转成账户初始值，并给出 2b 压降清单。

    口径（已与用户确认）：
    - ``balance = min(次月结余, 2b)``，超出部分作废并列入压降清单；
    - ``allowance`` 原样保留（独立桶，不受 2b 限制）；
    - 表格末列「结余」只作核对参考，不参与入账。
    """
    accounts: list[dict] = []
    capped: list[dict] = []
    mismatched: list[dict] = []

    for row in rows:
        raw = int(row.get("balance_raw") or 0)
        allowance = int(row.get("allowance") or 0)
        balance = min(raw, BALANCE_CAP)
        if raw > BALANCE_CAP:
            capped.append({
                "character": row["character"],
                "declared": raw,
                "applied": balance,
                "wasted": raw - balance,
            })
        accounts.append({
            "character": row["character"],
            "balance": balance,
            "monthly_reward": int(row.get("monthly_reward") or 0),
            "allowance": allowance,
        })
        # 末列核对：结余 应等于 次月结余 + 本月额度 - 消费（消费未记录，故仅提示差异）
        declared = int(row.get("total_declared") or 0)
        expected = raw + int(row.get("monthly_reward") or 0) + allowance
        if declared and declared != expected:
            mismatched.append({
                "character": row["character"],
                "declared": declared,
                "expected": expected,
                "diff": declared - expected,
            })

    return {
        "accounts": accounts,
        "capped": capped,
        "mismatched": mismatched,
        "total_balance": sum(a["balance"] for a in accounts),
        "total_allowance": sum(a["allowance"] for a in accounts),
        "total_wasted": sum(c["wasted"] for c in capped),
    }
