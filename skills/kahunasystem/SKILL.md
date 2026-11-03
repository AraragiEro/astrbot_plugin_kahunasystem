---
name: kahunasystem
description: 当用户需要查询 EVE Online 物品价格、计算制造成本与利润率、分析市场成交量与挂单深度、询问造什么更赚钱或该排多少产能、查询关注星系系数、估价游戏内文本、查询计划蓝图缺失状态，或查询本人成员银行账户与下单，配船相关问题时使用。
---

# 重要：身份与调用方式

1. **没有通用转发入口**。不存在 `kahunasystem_apiinfo` / `kahunasystem_apirun`，
   也不要找 `/api/astrbot/*`。直接按下面的工具名调用 MCP 工具。
2. **带 `qq` 参数的工具必须先取号**：先调用 `kahunasystem_get_my_qq` 拿到当前会话用户的真实 QQ，
   再把返回的号码传进去。不要猜测，不要使用其他用户的 QQ。
3. **物品名不确定时先匹配**：调用 `market_fuzz_type_name` 拿候选，让用户确认后再查，
   不要自己拼写或猜英文名。
4. **市场区域**：`market_zone` 一般填 `jita` 或 `frt`，也接受星系名/星系 ID（会解析为其所属区域）。
   传了无法识别的区域时工具会返回候选，**不会静默回退**，按返回值提示用户。

# 工具速查

| 工具 | 用途 | 关键参数 |
|---|---|---|
| `kahunasystem_get_my_qq` | 取当前用户 QQ（插件侧工具，无参数） | — |
| `market_fuzz_type_name` | 物品名模糊匹配 | `type_name` |
| `market_price_detail` | 单个物品当前买卖价 + 7/30 天汇总 | `type_name`, `market_zone` |
| `market_type_cost` | 生产/制造成本明细 | `type_name`, `qq`, `plan_name`, `force_recalculate` |
| `market_series` | 历史成交量/价格（指标包或序列） | `type_name`, `market_zone`, `days`, `detail`, `fields` |
| `market_depth` | 订单簿快照：价差 / 挂单深度 / 深度天数 | `type_name`, `market_zone`, `levels` |
| `market_compare` | 多市场量价对照（默认 jita,frt） | `type_name`, `market_zones`, `days` |
| `market_fit` | 产能匹配：市场能吃多少 → 该排多少 | `type_name`, `target_share`（必填）, `monthly_capacity`, `unit_cost` |
| `market_tag_list` | 用户的自选市场标签 | `qq` |
| `market_type_metrics` | 自选市场里的物品关键指标 | `qq`, `market_id`, `market_zone` |
| `plan_missing_blueprint_workflow_summary` | 计划缺失蓝图汇总 | `qq`, `planname` |
| `watched_system_coefficients` | 关注星系的制造/反应系数 | `qq` |
| `running_jobs_overview` | 运行中的制造/反应任务概览 | `qq` |
| `price_estimate` | 粘贴 EVE 文本自动估价 | `text`, `coefficient`, `auto_calculate_cost` |
| `qq_vip_state` | 用户 VIP 状态 | `qq` |
| `angel_recall` | 知识库与记忆库检索 | — |

# 场景路由

## 查某个物品的价格
1. 名字不确定 → `market_fuzz_type_name`
2. `market_price_detail(type_name, market_zone="jita")`
3. 需要"好不好卖 / 能卖多少" → 再调 `market_depth`

## 算某个物品的成本
1. 名字不确定 → `market_fuzz_type_name`
2. 先 `kahunasystem_get_my_qq`，再 `market_type_cost(type_name, qq=...)`
   —— 传 qq 是为了用对应用户的成本缓存作用域，不传会落到公共作用域

## 算利润
1. 成本：`market_type_cost`
2. 价格：`market_price_detail`
3. 利润 = 售价 − 成本；给结论时说明用的是买价还是卖价基准

## 分析市场 / 造什么更赚钱
1. 先 `kahunasystem_get_my_qq`，再 `market_tag_list(qq)` 拿用户自己的市场标签
   —— 用户说的 tag 不在列表里时，**先向用户确认**再继续
2. 列出准备使用的参数（市场、成本模式、价格基准）并问用户是否调整
3. `market_type_metrics(qq, market_id, ...)` 取该市场的关键指标
4. 从利润率、利润绝对值、月利润空间、是否已有库存或在产等方面给推荐

## 判断某物品能不能吃得下这个量
- 用 `market_fit`，**不要**自己拿年销量 ÷ 12。
- `target_share` 是必填的经营假设（0~0.5），拿不到就向用户问。
- 结论里要带上工具回显的 `assumptions`。

## 比较两个市场的差价
- `market_compare(type_name, market_zones="jita,frt", days=90)`
- 第一个市场是基准，其余给 `volume_ratio`（量比）和 `price_premium_pct`（溢价%）。

## 长期趋势 / 成交量分析
- 优先 `market_series(detail="summary")`：
  含中位数、分位、趋势斜率、动量、周末效应、量价相关、异常日、月度序列，
  体积约为逐日明细的 1/25。
- 需要看形状再 `detail="monthly"`；只有用户追问"哪几天"才 `detail="daily"` 且
  `fields="volume"`。
- ⚠️ `detail="daily"` + `days=365` 约 19k tokens，不要为"分析"拉全年明细。

## 计划蓝图缺失
1. `kahunasystem_get_my_qq` → `plan_missing_blueprint_workflow_summary(qq, planname)`
2. 解析与输出规范 → `references/skill_missing_blueprint_workflow_display.md`

## 关注星系系数
- `watched_system_coefficients(qq)`：`manufacturing` / `reaction` 是小数，
  展示时 ×100 加百分号（0.023 → 2.30%）；`null` 表示暂无数据。

## 估价（合同 / 仓库 / 配装文本）
- `price_estimate(text, coefficient, auto_calculate_cost)`
- **必须把返回的 `share_url` 给用户**，方便查看完整结果和分享。
- `auto_calculate_cost=true` 会等成本算完（可能几分钟），用户没要求就别开。

## 运行中任务 / VIP 状态
- `running_jobs_overview(qq)`：制造、反应任务统计
- `qq_vip_state(qq)`：`vip_alpha` / `vip_omega` / `Free`

## 成员银行（插件侧工具）
成员查自己的账户余额、提交消费订单，都是**当前会话发送者本人**，不接受指定他人：

| 工具 | 用途 |
|---|---|
| `bank_my_account` | 查本人余额 / 额外津贴 / 当月奖励 + 最近流水 |
| `bank_create_order` | 下单（物品 + 数量），金额等管理员确认 |
| `bank_my_orders` | 查本人订单状态 |

# 配船
- 加载NoobBaoziFit技能
- 知识库使用(angel_recall)
- 注意工具调用轮数限制为10次

管理员另有 `bank_admin_*` 系列（待处理订单、确认扣款、驳回、账户总览）。
下单**不要**询问或填写价格，也不要提前替用户判断余额是否够。
