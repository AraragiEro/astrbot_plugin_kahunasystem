---
name: kahunasystem
description: 当用户需要查询 EVE Online 物品价格、计算制造成本与利润率、询问当前造什么更赚钱或需要生产推荐，以及查询计划蓝图缺失状态、公司医保额度时使用。先调用 kahunasystem_apiinfo 确认必要参数，再按下方场景路由调用 kahunasystem_apirun。
---

**重要**
使用kahunasystem_apirun前必须调用kahunasystem_apiinfo确认必要的参数

# 场景路由
## 当要查询某个物品价格时
1. 通过api获取正确的typename，参数要使用用户的原始输入。
2. 通过api获取价格

## 当要计算某个物品的成本时
1. 通过api获取正确的typename，参数要使用用户的原始输入。
2. 通过api获取成本

## 当要计算某个物品的利润时+
1. 通过api获取正确的typename，参数要使用用户的原始输入。
2. 通过api获取成本
3. 通过api获取价格
4. 计算利润

## 当询问市场或造什么更赚钱时，或寻求推荐的制造产品时，应该从用户的自选市场获取数据进行分析
1. 通过api查询当前用户拥有的自选市场,若用户询问的市场tag并不属于用户，向用户确认正确的市场tag
2. 列出将要用于计算市场关键指标的参数，询问是否有变更。
3. 通过api使用默认参数或用户指定的参数，获取市场关键指标
4. 从利润率，利润绝对值，出单月利润空间，是否有库存或已计划生产等方面分析推荐生产的产品

## 当用户询问计划的蓝图状态、缺失蓝图数量、发明计划相关信息时
1. 通过api获取信息
2. 处理和输出规范 -> `references/skill_missing_blueprint_workflow_display.md`

## 当用户在群内查询自己的医保额度时
1. 通过api调用 `get_company_medica_vouchers` 获取公司医保额度清单。
2. 从群昵称中提取角色名：优先取 `/` 后半段，例如 `SETCR-Jack/KomeijiKoishi514` 的角色名是 `KomeijiKoishi514`。
3. 按角色名在医保额度清单中匹配对应额度。
4. 输出规范 -> `references/skill_company_medica_vouchers_lookup.md`
