# -*- coding: utf-8 -*-
"""成员银行账户系统（PAP 结算）。

- ``settlement``：纯结算引擎（无依赖，可单测）
- ``db``：SQLite 账本存储
- ``pap``：SeAT PAP 采集（复用 seat_auth 会话）
- ``service``：业务编排（结算、消费、订单）
"""

from .settlement import (  # noqa: F401
    BALANCE_CAP,
    PAP_CAP,
    PAP_START,
    REWARD_MAX,
    REWARD_MIN,
    Account,
    ConsumeResult,
    GrantLine,
    RolloverResult,
    consume,
    grant_reward,
    plan_grants,
    reward_for_pap,
    rollover,
)

__all__ = [
    "BALANCE_CAP",
    "PAP_CAP",
    "PAP_START",
    "REWARD_MAX",
    "REWARD_MIN",
    "Account",
    "ConsumeResult",
    "GrantLine",
    "RolloverResult",
    "consume",
    "grant_reward",
    "plan_grants",
    "reward_for_pap",
    "rollover",
]
