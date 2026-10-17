### 0.0.10
修复接口异常时把后端整页 HTML 回显到 QQ 群的问题
- src/event.py 新增 api_error_for_user()：.ojita/.frt/.成本 的报错在群里只回固定文案「XX 暂时不可用，请稍后再试。」，状态码/后端 message/响应体一律不外发
- src/api_client.py 新增 ApiError：str(e) 只含「状态码 + 极短摘要」（供日志），原始响应体仅进日志并截断 500 字；HTML 响应打码、JSON 只取 message 字段；200 但非 JSON 也归入同一异常
- src/api_client.py api_qq_bind / api_qq_vip_state 补上 X-Internal-API-Key 请求头（此前未携带，绑定指令必然 401）

### 0.0.2
增加username和plan配置
