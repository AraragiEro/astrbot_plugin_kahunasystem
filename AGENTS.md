# AGENTS 说明
本文件用于约束本仓库的协作规范与输出偏好，适用于所有新增或修改的工作。

## 项目概况
- **插件名称**: kahunasystem_api (v0.0.5)
- **类型**: AstrBot 插件
- **用途**: QQ 机器人市场查价、成本计算、用户绑定、抽奖系统
- **后端依赖**: 通过 REST API 调用 **EveKahunaSystem**（Quart Web 应用，源码仓库 `../EveKahunaSystem-Dev`；
  旧目录名 `astrbot_plugin_kahunabot`、旧称 `kahunabot`，均指同一后端）获取数据
- **技术栈**: AstrBot API, aiohttp, Pillow (图片渲染), ESI API, zKillboard API

## 项目结构
```
./
├── main.py            # 插件入口，指令注册 (MyPlugin)
├── metadata.yaml      # 插件元数据
├── config.ini         # 本地配置文件
├── requirements.txt   # Python 依赖
├── src/
│   ├── event.py       # 指令处理逻辑 (.ojita/.frt/.成本/.绑定)
│   ├── mcp_client.py  # MCP 客户端（SSE + JSON-RPC），查价/成本取数
│   ├── api_client.py  # HTTP 客户端（仅 QQ 绑定：/api/plugin/qq/bind）
│   ├── seat_*.py      # SeAT 名单 / 名片同步 / 踢人 / 入群审核
│   ├── resource/      # 静态资源 (图片素材)
│   └── picture_render_server/  # 价格/成本图片渲染
├── data/              # 数据文件
├── skills/            # AstrBot 技能配置
└── prompt/            # AI prompt 模板
```

## QQ 群指令一览

| 指令 | 功能 | 权限 |
|------|------|------|
| `.ojita <物品名> [数量]` | 查询 Jita 市场价格，含图表渲染 | 公开 |
| `.frt <物品名> [数量]` | 查询 FRT 联盟市场价格 | 公开 |
| `.成本 <物品名>` | 查询制造成本明细 (图片渲染) | 公开 (全局锁防并发) |
| `cost <物品名>` | `.成本` 的英文别名 | 公开 |
| `.绑定kahunasystem <uuid>` | QQ 绑定 kahunasystem 账号 | 公开 |

> 指令前缀由 AstrBot 的 `wake_prefix` 决定（默认 `/`）；表中 `.绑定kahunasystem` 沿用历史写法。
> openid ↔ QQ 号的映射层已回退移除，业务身份直接用事件发送者 id（OneBot 平台即真实 QQ 号）。

## 关键开发模式

### 异步事件处理
```python
# 所有指令方法为 async，使用 AstrBot 的 @filter.command 装饰器
@filter.command("ojita")
async def cost(self, event: AstrMessageEvent, require_str: str):
    yield await Event.oprice(event, require_str)
```
- 返回值使用 `yield event.plain_result()` 或 `yield event.chain_result()`
- 图片使用 `Image.fromFileSystem(path)` + `chain_result` 返回

### 数据通道（MCP / HTTP 分工）
- **读能力走 MCP**：`.ojita` / `.frt` / `.成本` 通过 `src/mcp_client.py` 调用 MCP tools
  （SSE；`mcp_url` 配置，默认 `http://127.0.0.1:9000/sse`）
- **写能力走 HTTP**：`.绑定kahunasystem` 通过 `src/api_client.py` 调
  `POST /api/plugin/qq/bind`（服务密钥认证）
- `src/mcp_client.py` 用 aiohttp 手写最小 SSE + JSON-RPC，**不要**引入 mcp SDK
  （核心依赖版本保护会拦截与核心冲突的固定版本依赖）
- 🔴 `/api/astrbot/*`（api_info 取 token → api_run 转发）已全面日落淘汰，不要再新增调用
- 🔴 **接口失败不要在群里回细节**：用户可见文案统一走 `src/event.py` 的
  `api_error_for_user()`，只回固定文案「XX 暂时不可用，请稍后再试。」——
  状态码、后端 message、响应体一律不外发。
  `src/api_client.py` 的 `ApiError`（`ValueError` 子类）负责把异常文本收口：
  `str(e)` 只含「状态码 + 极短摘要」，原始响应体只进日志且截断 500 字
  （`_log_failure`），HTML 响应直接打码、JSON 只取 `message` 字段。
  原因：后端/网关（nginx、Cloudflare）出错时返回的是整页 HTML，几十 KB 拼进
  `plain_result` 会把整页 HTML/JS 灌进 QQ 群（2026-10-17 线上事故：
  `成本接口请求失败: HTTP 405, message: <!doctype html>…`）。排查看 AstrBot 日志。

### 图片渲染
- 使用 `picture_render_server/render_template.py` 生成价格/成本图表
- 返回本地文件路径，通过 `Image.fromFileSystem` 发送

### 图片渲染的依赖策略（重要）
- 出图链路：本地 Jinja2 渲染 `.j2` 模板 → **AstrBot 内置文转图服务**（`context.html_renderer`，
  走全局配置 `t2i_endpoint`，为空则用官方端点池）→ 落盘图片路径
- 传给文转图服务的 HTML 已渲染完成，必须整段包在 `{% raw %}` 里，防止服务端 Jinja2
  二次解析模板中 JS/CSS 的花括号
- **不要**把 `pyppeteer` / `imgkit` 写进 `requirements.txt`：pyppeteer 2.0.0 依赖
  `websockets<11`，与 AstrBot 核心的 `websockets>=15` 冲突，会被「核心依赖版本保护」
  拦截并导致整份依赖安装失败、插件加载失败（v4.29.0 踩过）
- 降级顺序：远程文转图 → 本地 pyppeteer（仅在环境里恰好可用时）→ 纯文本结果
  （`src/event.py` 的 `price_summary` / `cost_summary`）
- 远程渲染固定 800px 视口宽（`viewport` 选项会被忽略），`full_page=True` 时高度自适应；
  出图后用 `PictureRender.crop_to_width()` 本地裁回目标宽度，注意按模板内容对齐方式传
  `crop_align`：价格模板内容是居中卡片（`center`），成本模板内容靠左（`left`）

### QQ 官方机器人的 @（重要）
- QQ 官方机器人只解析**文本 / markdown** 消息里的 @ 标签（适配器把 `At` 组件序列化成
  `<qqbot-at-user id="..." />`）；**带图的消息走富媒体消息（msg_type=7），其中的 @ 标签
  不会被解析，会原样显示**成那串标签文本
- 因此出图指令（`.ojita`/`.frt`/`.成本`）统一先单独发一条「@ 发送者 + 文本摘要」
  （见 `src/event.py` 的 `send_mention_text()` / `prepare_image_reply()`），再发图片
- 图片链会插入一个空 `Reply` 占位，用于跳过 AstrBot 发送前装饰阶段对
  「全是 Plain/Image」消息链的 At 注入（`result_decorate/stage.py` 的 `can_decorate`）；
  qq_official 适配器会忽略空 `Reply`，所以图片消息不会再冒出标签
- AstrBot 的「回复时 @ 用户」(`platform_settings.reply_with_mention`) 对图片回复无效
  （正是上面那个原因），插件已在加载时就此打一条 INFO 日志提示
- 私聊（C2C）与 aiocqhttp 等平台不做此处理，@ 仍交给 AstrBot 的
  「回复时 @ 用户」设置

### 业务身份 = 发送者 id（重要）
- openid ↔ QQ 号映射层（`src/identity.py` 与 `绑定QQ / 解绑QQ / 我的绑定 / 绑定审计`）
  已**回退移除**：业务身份直接用 `event.get_sender_id()`，OneBot/aiocqhttp 平台即真实 QQ 号
- 取号统一走 `src/event.py` 的 `_sender_qq(event)`（非数字身份返回 `None`）：
  - `.绑定kahunasystem` 拿不到 QQ 号时提示「绑定功能暂时不可用」；
  - `.成本` 拿不到 QQ 号时省略 `qq` 参数，后端回落到 system 公共作用域
- **@ 提及继续用 `event.get_sender_id()`**：群场景下是同一个值，不要再造身份层
- QQ 官方机器人只提供 openid → 绑定不可用；不要再往插件里加映射表（已按需求淘汰）
- **给 LLM 的取号入口**：`@filter.llm_tool(name="kahunasystem_get_my_qq")`（`main.py`，
  逻辑在 `Event.get_my_qq_for_llm()`），直接返回发送者 id。模型在调用带 `qq` 参数的
  MCP 工具**之前**应先调用它。写工具时注意：
  - **必须 return str**：返回 `None` 表示「结果不进下一轮 prompt」，模型看不到
  - **不要 yield**：工具里 yield 的内容会被当成要给用户发的消息
  - 工具函数第一个参数是 `self`，第二个是 `event`；参数 schema 由**函数注释**解析
  - 只能解析「当前会话的发送者」，无法解析其他人

### 配置要求
```ini
# 插件配置（AstrBot 面板 → _conf_schema.json）
mcp_url = "http://127.0.0.1:9000/sse"        # 查价 / 成本走 MCP
kahunasystem_host = "http://localhost:9527"  # 仅 QQ 绑定使用
service_api_key = "<与后端 [SERVICE] API_KEY 一致>"
cost_plan = "default_plan"                   # 成本计算默认方案
```

# 基本规则
- 语言优先中文，除非用户明确要求英文
- 注意中文输出不要输出乱码
- 遵循现有代码风格与目录结构，减少无关重构
- 修改范围以任务需求为边界，不做“顺手优化”
- 保持可读性，必要时补充简短注释
- 重要改动要说明原因与影响
- 所有修改后的文本文件需使用 UTF-8 编码保存，避免中文注释与日志乱码
- 新增或修改日志/注释时，优先直接使用标准中文，禁止复制粘贴乱码内容
- 若发现乱码，按以下步骤处理并记录：
  1. 确认编辑器当前编码为 UTF-8（无 BOM 优先）
  2. 重新以 UTF-8 重新保存文件
  3. 仍有乱码时，用脚本全量重写该文件内容并确认日志/注释可读

# Commit Message 规范
- 使用中文
- 标题简短清晰，描述改动目标
- 需要列出涉及的文件与关键改动点
- 多文件协作改动请在同一条目中说明关联关系
- 格式:
  - `[类型] 简短中文描述`
  - `1. 文件A 关键改动`
  - `2. 文件B 文件C 关联改动`
- 示例:
  - `feat: 新增用户注册`
  - `1. src/user.py 增加注册入口`
  - `2. src/auth.py src/db.py 增加注册校验与存储`

# 运行与环境
- 开发环境为 Windows
- 命令以 PowerShell 可用为准

# 项目概况
- **插件名**: kahunasystem_api v0.0.5
- **类型**: AstrBot 插件（QQ 机器人）
- **功能**: 市场查价（.ojita/.frt/.成本）、QQ 绑定、抽奖系统
- **依赖后端**: **EveKahunaSystem**（Quart Web 应用，源码仓库 `../EveKahunaSystem-Dev`；
  旧目录名 `astrbot_plugin_kahunabot`、旧称 `kahunabot`）提供 REST API

# 目录结构
```
astrbot_plugin_kahunasystem/
├── main.py              # 插件入口，指令注册
├── metadata.yaml        # AstrBot 插件元数据
├── requirements.txt     # 依赖列表
├── config.ini           # AstrBot 配置文件
├── src/
│   ├── event.py         # 事件处理器（oprice/frt_price/costdetail/bind）
│   ├── mcp_client.py    # MCP 客户端（SSE + JSON-RPC）
│   ├── api_client.py    # HTTP 客户端（仅 QQ 绑定）
│   ├── seat_*.py        # SeAT 名单 / 名片 / 踢人 / 入群审核
│   ├── picture_render_server/  # HTML → 图片渲染服务
│   └── resource/        # 静态资源
└── data/                # 数据文件
```

# QQ 指令

| 指令 | 功能 | 示例 |
|------|------|------|
| `.ojita <物品> [数量]` | 查询 Jita 市场价格 | `.ojita Tritanium 100` |
| `.frt <物品> [数量]` | 查询 FRT 市场价格 | `.frt Ishtar` |
| `.成本 <物品>` | 查询物品生产成本 | `.成本 Megathron` |
| `.绑定kahunasystem <uuid>` | 绑定 QQ 号到 Kahuna 账号 | `.绑定kahunasystem abc123` |

# API 集成模式

**调用流程**：
```
查价/成本：QQ 指令 → Event.xxx() → mcp_client.call_tool() → MCP 中间层 → 后端
QQ 绑定：  QQ 指令 → Event.bind_kahunasystem() → api_client.bind_qq() → /api/plugin/qq/bind
```

- **读能力**（`market_price_detail` / `market_type_cost`）通过 `src/mcp_client.py` 调 MCP tools
- **写能力**（QQ 绑定）通过 `src/api_client.py` 调 HTTP，服务密钥认证
- 🔴 `/api/astrbot/*` 已全面日落淘汰，后端不再提供 api_info / api_run
- 价格/成本结果通过 HTML 渲染为图片返回 QQ

# 关键模式

## 错误处理
```python
try:
    res_json = await api_xxx(...)
except Exception as e:
    logger.error(f"xxx接口请求异常: {e}")          # 完整信息只进日志
    return event.plain_result(api_error_for_user("xxx接口"))  # 群里只回固定文案
```
- **禁止**把异常原文（`f"...{e}"`）拼进 `plain_result`，也不要回状态码、后端 message：
  后端/网关的 HTML 错误页会把整页 HTML/JS 发到群里。

## 图片渲染
- `picture_render_server/render_template.py` 提供 `render_price_res_pic()` / `render_single_cost_pic()`
- 返回临时图片路径，通过 `Image.fromFileSystem()` 发送到 QQ 群

## 并发控制
- 成本计算（`.成本`）使用 `asyncio.Lock` 保证同一时间只有一个计算任务
- 其他指令无并发限制

# 配置要求
- `mcp_url`: MCP 中间层地址（默认 `http://127.0.0.1:9000/sse`），查价/成本走它
- `kahunasystem_host` + `service_api_key`: 仅 QQ 绑定使用
- `cost_plan`: 成本计算默认方案名（`cost_username` 已废弃，不再生效）
- 配置由 AstrBot 管理面板注入到 `AstrBotConfig`

# 计划与任务
- 中等及以上复杂度任务需要拆分为 todos
- todo 项要可执行、可验收

# 注意事项
- 新增指令遵循 `@filter.command` 装饰器模式，参考 `main.py` 中已有指令
- 读数据统一走 `src/mcp_client.py` 的 MCP tools，**不要**把后端 HTTP 调用塞回插件
- 写操作（QQ 绑定）走 `src/api_client.py` 的 `/api/plugin/qq/bind`
- 图片渲染失败时降级为纯文本提示，不阻塞响应
