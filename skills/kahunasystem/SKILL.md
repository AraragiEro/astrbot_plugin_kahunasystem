---
name: kahunasystem
description: KahunaSystem 统一入口 API 使用说明，用于确认可用能力并获取详情后调用。
---

当需要确认 KahunaSystem 是否提供某项能力并完成调用时使用本说明。

## 前置条件
- 已配置 `kahunasystem_host`（如 `127.0.0.1:9999`）
- 可选：已配置 `cost_username` 与 `cost_plan`

## 统一入口
- `GET /api/astrbot/kahunasystem/api/list`
- `POST /api/astrbot/kahunasystem/api/info`
- `POST /api/astrbot/kahunasystem/api/run`

统一调用 body：
```json
{
  "api_id": "xxx",
  "args": {
    "...": "..."
  }
}
```

## 使用方法

### 1) apilist
先列出 kahunasystem 当前提供的 `api_id` 清单。
```json
{}
```

返回示例（以实际为准）：
```json
{
  "status": 200,
  "data": [
    {"entry_id": "xxx", "description": "..."}
  ]
}
```

### 2) apiinfo
确认需要调用哪些 `api_id` 后，获取详细参数与示例。
```json
{
  "api_ids": ["api_id_1", "api_id_2"]
}
```

返回示例（以实际为准）：
```json
{
  "status": 200,
  "data": [
    {
      "id": "api_id_1",
      "description": "...",
      "args": {"field": "type"},
      "args_example": {"field": "value"},
      "res_example": {"...": "..."}
    }
  ]
}
```

### 3) apirun
按 `api_id` 调用对应能力。
```json
{
  "api_id": "api_id_1",
  "args": {
    "field": "value"
  }
}
```

## 注意事项
- `api_id` 必填；`args` 必须是对象（dict），否则会返回 400。
- 业务参数保持不变，只是被包裹在 `args` 中。
- 返回结构与原业务接口基本一致。
