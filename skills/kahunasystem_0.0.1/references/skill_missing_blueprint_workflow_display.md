# SKILL 内部调用说明：缺失蓝图汇总展示

## 适用接口
- `plan_missing_blueprint_workflow_summary`
- 读取字段：`data.missing_blueprint_workflow`

## 目标（只展示两块）
1. 先展示「舰船的 T2 发明流程」：明确 `T2 船 <- T1 船` 对应关系。
2. 再展示分类统计：`T1船`、`组件`、`反应` 的缺失蓝图数量。
- 除以上内容外，不展示其他信息。

## 数据解析规则
1. 从 `missing_blueprint_workflow` 逐条读取节点。
2. 使用 `missing_blueprint_type` 分类：
- `舰船` / `组件` / `反应` / `其他`
3. 舰船节点里，仅当满足以下条件时，认定为可展示的 T2 发明关系：
- `tech2_invention_source_bp_type_id` 有值
- `tech2_invention_source_bp_type_name` 有值
4. 对舰船节点输出映射：
- `T2船名 = type_name_zh`
- `T1船名 = tech2_invention_source_bp_type_name`
5. 统计计数：
- `T1船缺失蓝图数 = 舰船节点中“有 T1 来源”的数量`
- `组件缺失蓝图数 = missing_blueprint_type == 组件 的数量`
- `反应缺失蓝图数 = missing_blueprint_type == 反应 的数量`
- 忽略 `其他`，不展示。

## 展示顺序与格式
### 1) 舰船 T2 发明流程（优先展示）
- 标题：`舰船 T2 发明流程`
- 每行格式：`- {T2船名} <- {T1船名}`
- 建议按 `T2船名` 升序。
- 若无可展示关系：显示 `- 无`

### 2) 缺失蓝图分类统计
- 标题：`缺失蓝图分类统计`
- 固定三行：
- `- T1船：{count_t1_ship}`
- `- 组件：{count_component}`
- `- 反应：{count_reaction}`

## 参考伪代码
```python
workflow = resp.get("data", {}).get("missing_blueprint_workflow", []) or []

ship_pairs = []
count_component = 0
count_reaction = 0

for node in workflow:
    t = node.get("missing_blueprint_type")
    if t == "舰船":
        t2_name = node.get("type_name_zh") or node.get("type_name") or ""
        t1_name = node.get("tech2_invention_source_bp_type_name")
        if t2_name and t1_name:
            ship_pairs.append((t2_name, t1_name))
    elif t == "组件":
        count_component += 1
    elif t == "反应":
        count_reaction += 1

ship_pairs.sort(key=lambda x: x[0])
count_t1_ship = len(ship_pairs)
```

## 输出示例
```text
舰船 T2 发明流程
- 伊什塔级 <- 托勒克斯级
- 猛鲑级 <- 卡拉卡尔级

缺失蓝图分类统计
- T1船：2
- 组件：5
- 反应：3
```
