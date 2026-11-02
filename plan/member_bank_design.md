# 成员银行账户系统（PAP 结算）设计定稿

> 落点：**全部在 `astrbot_plugin_kahunasystem` 插件内**，不依赖也不修改 EveKahunaSystem。
> 本文档为该功能的唯一口径来源；实现与本文冲突时以本文为准，改口径先改本文。

## 1. 数据来源（已实测，2026-11-02）

SeAT 的 paptracking 页面用 DataTables 服务端模式，接口同时是名单和 PAP 来源：

```
GET https://seat.winterco.org/tools/paptracking?draw=1&start=0&length=500&year=2026&month=10
```

实测结论：

| 项 | 结论 |
|---|---|
| 鉴权 | 复用插件已保存的 `seat:session` Cookie（`src/seat_auth.py`） |
| 历史月份 | **支持** `?year=YYYY&month=M`，实测 2025-11 ~ 2026-11 全部可取 |
| 不传参数 | 返回**上个月**（不能依赖，结算必须显式传参，否则口径随日期漂移） |
| 返回行数 | 当前 95 人（全公司成员，非仅活跃者） |
| 关键字段 | `character` / `nickname` / `qq_id` / `pap_count` / `strat_pap_count` |
| Start PAP | `strat_pap_count`（恒 ≤ `pap_count`），后缀拼写是 **strat** 不是 start |
| 数值类型 | **字符串小数**，0.5 步进（如 `"107.50"`），需自行转 float |
| QQ 覆盖 | 95 人中 90 人填了 `qq_id`，5 人缺失 |

PAP 数据是**月度增量**，不是累计（已交叉验证各月最大值在 19.5~107.5 之间波动）。

## 2. 金额单位与类型

- 内部一律 **ISK 整数**（`INTEGER`），禁止浮点。ISK 无小数。
- `1e` = 1 亿 = `100_000_000`；`1b` = 10 亿 = `1_000_000_000`。
- PAP 奖励区间：2e = `200_000_000` ~ 5e = `500_000_000`。
- 余额封顶 `CAP = 2_000_000_000`（2b）。

## 3. 结算规则（已定稿）

### 3.1 奖励档位

```
pap_int = floor(strat_pap_count)          # 向下取整，9.5 -> 9
pap_int < 6            -> reward = 0
6 <= pap_int <= 15     -> reward = min(5e, 2e + ((pap_int - 6) // 3) * 1e)
pap_int > 15           -> reward = 5e
```

| PAP(int) | 6 | 8 | 9 | 11 | 12 | 14 | 15+ |
|---|---|---|---|---|---|---|---|
| 奖励 | 2e | 2e | 3e | 3e | 4e | 4e | 5e |

### 3.2 三桶模型

| 桶 | 字段 | 上限 | 跨月 |
|---|---|---|---|
| 当月奖励 | `monthly_reward` | — | 月末并入 `balance`，归零 |
| 额外津贴 | `allowance` | **无上限** | **永不清零**，不计入次月结余 |
| 余额 | `balance` | **2b 硬上限** | 结转 |

消费顺序：`monthly_reward` → `allowance` → `balance`。

月末结转：

```
new_balance = min(balance + monthly_reward, CAP)
wasted      = balance + monthly_reward - new_balance    # 超出部分作废
balance     = new_balance
monthly_reward = 0
allowance   = allowance                                  # 原样，不受影响
```

### 3.3 发放范围

- **全部 SeAT 成员**：PAP 达标即发，无账户的自动建档（`status=active`）。
- `qq_id` 为空的成员**跳过**，并在结算报告中列为 `unclaimed`，等待人工绑定。
- 历史导入时 `balance > 2b` 的，压到 2b 并生成 `wasted` 明细清单，不静默改账。

### 3.4 超支

消费时三桶合计不足 → **拒绝订单**，不产生负余额。

### 3.5 幂等与重入

- 每个 `(period, qq_id, kind)` 对应唯一 `idempotency_key`，重复执行不重复发放。
- `settlement_run` 表记录每个结算月的状态（`preview` / `applied`），已 `applied` 的月份再次执行默认拒绝，需显式 `force` 且先出冲正。
- 上线节奏：**先手动触发 + dry-run 预览**，连续两个月的预览报表对账无误后再开定时任务。

## 4. 表结构（SQLite，`<data_dir>/bank.db`）

`data_dir = StarTools.get_data_dir("astrbot_plugin_kahunasystem")`
= `/AstrBot/data/plugin_data/astrbot_plugin_kahunasystem`（挂载卷，插件更新不丢）。

```sql
member_account   (qq_id PK, character, balance, allowance, monthly_reward,
                  status, created_at, updated_at)
pap_snapshot     (period, qq_id, character, pap_count, strat_pap_count,
                  source, fetched_at, PK(period, qq_id))
ledger           (id PK, qq_id, period, kind, amount, balance_after,
                  allowance_after, order_id, operator, idempotency_key UNIQUE,
                  note, created_at)
member_order     (id PK, qq_id, character, item, quantity, status, amount,
                  confirmed_by, confirmed_at, created_at)
settlement_run   (period PK, status, total_reward, member_count, operator, created_at)
```

`balance` 必须可由 `ledger` 重算对账（`ledger` 只追加、不修改、不删除）。

## 5. 接口与入口

| 入口 | 形态 | 说明 |
|---|---|---|
| 成员查账户 | LLM 工具 / 指令 | 只能查自己（身份取自 `event.get_sender_id()`，不接受他人参数） |
| 成员下单 | LLM 工具 | 提供物品 + 数量，落 `member_order(status=pending)` |
| 管理员查订单 | LLM 工具 / Web | 列表 + 详情 |
| 管理员确认 | LLM 工具 / Web | 填入消费额度 → 扣减 + `status=confirmed`（同事务） |
| 管理员驳回 | LLM 工具 / Web | `status=rejected`，不动账 |
| 结算 | 指令 / Web | `dry-run` 预览 → `apply` 落账 |
| 历史导入 | Web 贴表格 | 解析 4 列表格，输出压降清单 |

管理员身份校验：以发送者 QQ 在管理员白名单里为准，**不接受工具参数里的"我是管理员"**。

## 6. 验收

1. 单测覆盖：档位边界（0/5/6/8/9/14/15/16/107.5）、2b 封顶作废、津贴跨月不清零、消费三桶顺序、超支拒绝、重复结算幂等。
2. 干跑 2026-10 全量名单，`sum(reward)` 与实际发放额一致。
3. 导入 38 行历史后，与人工核算逐行一致（含 4 行压降明细）。
4. 一单完整链路：下单 → 确认 → 扣减 → 流水可追溯。
