---
name: kahunasystem
description: EVE online相关的工具，涉及物品名匹配，价格查找，成本计算。
---

当要查询某个物品价格时
1. 通过api获取正确的typename，参数要使用用户的原始输入。
2. 通过api获取价格

当要计算某个物品的成本时
1. 通过api获取正确的typename，参数要使用用户的原始输入。
2. 通过api获取成本

当要计算某个物品的利润时+
1. 通过api获取正确的typename，参数要使用用户的原始输入。
2. 通过api获取成本
3. 通过api获取价格
4. 计算利润