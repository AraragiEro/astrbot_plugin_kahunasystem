### 未发布（查价链路迁移 MCP + 身份映射回退）
配合后端淘汰 `/api/astrbot/*`，读能力改走 MCP，并回退 openid↔QQ 映射层
- 新增 src/mcp_client.py：只用 aiohttp 手写最小 SSE + JSON-RPC 客户端（不引入 mcp SDK，避免核心依赖版本冲突），`.ojita` / `.frt` / `.成本` 改为调用 MCP tools `market_price_detail` / `market_type_cost`
- `.绑定kahunasystem` 保留为 HTTP 调用，改走新接口 `POST /api/plugin/qq/bind`（服务密钥认证），不经过 MCP
- 删除 src/identity.py 与 `绑定QQ / 解绑QQ / 我的绑定 / 绑定审计` 四条指令：业务身份直接用 `event.get_sender_id()`（OneBot 平台即真实 QQ 号）；`kahunasystem_get_my_qq` 保留但直接返回发送者 id
- `.成本` 不再传 `user_name`（成本作用域由后端按 QQ 绑定解析），拿不到 QQ 号时省略 `qq` 参数回落 system 作用域
- src/api_client.py 精简为只保留绑定调用（移除 api_info / api_run / api_price_detail / api_frt_price_detail / api_type_cost / api_qq_bind / api_qq_vip_state 及 zKill/ESI 残留封装）
- 配置新增 `mcp_url`（默认 `http://127.0.0.1:9000/sse`）；`kahunasystem_host` 仅绑定使用；`cost_username` 标记废弃
- **修复**：`mcp_url` 原默认值 `127.0.0.1:9000` 在 Docker 部署下必然连不上（MCP 实际是 9274，且插件与后端不在同一容器）→ 改为**留空自动推导**「`kahunasystem_host` 同主机 + 9274 + `/sse`」；同时修掉后端地址带尾斜杠时绑定接口拼出 `//api/plugin` 的问题

### 未发布（插件市场规范整理）
上架前置的元数据与规范整理，不改变运行时行为
- metadata.yaml 重写：name 改为 astrbot_plugin_kahunasystem（符合官方 `astrbot_plugin_` 前缀规范、与仓库名一致），补 display_name / short_desc / 详细 desc / tags / astrbot_version(>=4.13.0) / support_platforms / social_link；version 去掉 v 前缀
- main.py @register 由模板值（helloworld / YourName / 1.0.0）改为真实插件名、作者、描述与版本
- README.md 由 helloworld 模板重写为正式文档：功能、指令表、必填前置（自建后端 + 服务密钥）、配置说明、平台支持、数据与隐私
- _conf_schema.json 配置项描述改为中文并补 default / hint，service_api_key 增加 secret 遮罩
- skills/kahunasystem_0.0.1 改名为 skills/kahunasystem；两个 SKILL.md 的 frontmatter 修正为字符串类型 description（原写法在 frontmatter 内顶格写 `-` 列表项属非法 YAML，会导致技能描述解析失败）
- 新增 logo.png（256x256，1:1），供插件市场与 WebUI 展示
- .gitignore 放行 logo.png（原 `*.png` 规则会把它一并忽略）、忽略 resource/img 运行时缓存与 *.zip / .playwright-mcp / data/
- 移除冗余的 skills/*.zip 分发包与已无代码引用的 data/cmd_config.json
- requirements.txt 移除未使用的 requests（AstrBot 开发原则要求使用 aiohttp / httpx 等异步库）

### 0.0.10
修复接口异常时把后端整页 HTML 回显到 QQ 群的问题
- src/event.py 新增 api_error_for_user()：.ojita/.frt/.成本 的报错在群里只回固定文案「XX 暂时不可用，请稍后再试。」，状态码/后端 message/响应体一律不外发
- src/api_client.py 新增 ApiError：str(e) 只含「状态码 + 极短摘要」（供日志），原始响应体仅进日志并截断 500 字；HTML 响应打码、JSON 只取 message 字段；200 但非 JSON 也归入同一异常
- src/api_client.py api_qq_bind / api_qq_vip_state 补上 X-Internal-API-Key 请求头（此前未携带，绑定指令必然 401）

### 0.0.2
增加username和plan配置
