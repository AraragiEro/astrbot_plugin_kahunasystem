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
│   ├── api_client.py  # HTTP 客户端，封装对 kahunabot 的 API 调用
│   ├── identity.py    # 平台身份(openid) ↔ 真实 QQ 号 映射层
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
| `/绑定QQ <QQ号>` | 把当前平台身份绑定到你的 QQ 号（QQ 官方机器人必做一次） | 公开 |
| `/解绑QQ` | 解除当前身份的 QQ 号绑定 | 公开 |
| `/我的绑定` | 查看当前身份的绑定情况 | 公开 |
| `/绑定审计 <QQ号>` | 查看某 QQ 号的绑定/解绑审计 | 管理员 |

> 指令前缀由 AstrBot 的 `wake_prefix` 决定（默认 `/`，插件会把正确前缀拼进提示语）；
> 表中 `.绑定kahunasystem` 沿用历史写法，实际前缀以主人配置为准。

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

### API 客户端模式
- 所有外部 API 调用集中在 `src/api_client.py`
- 使用 aiohttp 异步请求
- `post_json()` / `get_json()` 为基础封装
- `api_run()` 为通用 kahunabot 后端调用入口
- 需先调用 `api_info()` 获取 access_token

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

### 平台身份与 QQ 号映射（重要）
- 背景：QQ 官方机器人**只提供 openid**（群 `member_openid` / 单聊 `user_openid`，
  两者是不同值），平台不提供 QQ 号也没有反查接口；而 kahunabot 后端与既有数据
  （`user_qq_binding`、资产归属、权限、缓存 key）全部以整数 QQ 号为业务身份
- 中间层 `src/identity.py`：业务需要 QQ 号时统一走 `resolve_qq(event)` ——
  纯数字身份（OneBot/aiocqhttp 等）直接返回，openid 查映射表；查不到时调用方用
  `qq_required_hint(event)` 提示用户绑定，**不要因此阻断不依赖 QQ 号的功能**（查价/出图）
- 存储：AstrBot 插件 KV（`Star.get_kv_data/put_kv_data`，底层 `AstrBot/data/data_v4.db`）。
  **不要**把映射写进插件目录的 `data/`，面板更新插件会把它覆盖掉
- 键结构：`uid2qq:<platform>:<scene>:<uid>`（记录 dict）、`qq2uid:<qq>`（唯一性校验）、
  `audit:<qq>`（绑定/解绑审计，最多 20 条）
- 唯一性规则：一个 QQ 号只能属于一个平台身份；一个身份同时只能绑一个号，换号需先解绑
- ⚠️ 群聊与单聊的 openid 不同 → 两个场景各自登记（私聊里绑定不影响群聊）；
  若实测发现同一用户在不同群的 `member_openid` 不一致，需要把群 ID 并入
  `IdentityStore.identity_key`
- ⚠️ **@ 提及必须继续用 openid**（`event.get_sender_id()`），只有业务身份才用解析出的
  QQ 号，两者不要混用
- 仅覆盖插件指令链路；kahunabot 侧的 MCP 工具（`qq_vip_state(qq)` 等）不经过本层，
  如后续要支持需另行处理
- **给 LLM 的取号入口**：`@filter.llm_tool(name="kahunasystem_get_my_qq")`（`main.py`，
  逻辑在 `Event.get_my_qq_for_llm()`）。模型在调用带 `qq` 参数的 kahunabot MCP 工具**之前**
  应先调用它拿到当前用户的真实 QQ 号。写工具时注意：
  - **必须 return str**：AstrBot 中 LLM 工具返回 `None` 表示「结果不进下一轮 prompt」，
    返回 str 才会给模型看到；返回的内容不会发到群里
  - **不要 yield**：工具里 yield 的内容会被当成要给用户发的消息
  - 工具函数第一个参数是 `self`（AstrBot 用 `functools.partial(handler, 插件实例)` 绑定），
    第二个是 `event`；参数 schema 由**函数注释**解析，有参数时必须写规范 `Args:` 段
  - 群聊/私聊 openid 不同，因此本工具只能解析「当前会话的发送者」，
    无法解析其他人（如把别人的 QQ 号传给它核对）

### 配置要求
```ini
# config.ini 必需字段
kahunasystem_host = "http://localhost:9527"  # kahunabot 后端地址
cost_username = "default_user"               # 成本计算默认用户
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
│   ├── api_client.py    # kahunabot REST API 封装 (aiohttp)
│   ├── identity.py      # 平台身份(openid) ↔ 真实 QQ 号 映射层
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
| `/绑定QQ <QQ号>` | 绑定当前平台身份到你的 QQ 号（QQ 官方机器人必做一次） | `/绑定QQ 123456789` |
| `/解绑QQ` | 解除当前身份的 QQ 号绑定 | `/解绑QQ` |
| `/我的绑定` | 查看当前身份的绑定情况 | `/我的绑定` |
| `/绑定审计 [QQ号]` | 查看绑定/解绑审计（仅管理员） | `/绑定审计 123456789` |

# API 集成模式

**调用流程**：
```
QQ 指令 → Event.xxx() → api_info() 获取 access_token → api_xxx() HTTP 调用 → kahunabot 后端
```

- 所有 API 调用先通过 `api_info()` 获取 JWT access_token
- API 客户端封装在 `src/api_client.py`，使用 `aiohttp` 异步 HTTP 请求
- 后端接口路径格式: `/api/astrbot/kahunasystem/<模块>/<操作>`
- 价格/成本结果通过 HTML 渲染为图片返回 QQ

# 关键模式

## 错误处理
```python
try:
    res_json = await api_xxx(...)
except Exception as e:
    if isinstance(e, ValueError):
        return event.plain_result(f"请求失败: {e}")
    return event.plain_result("请求异常，请稍后再试。")
```

## 图片渲染
- `picture_render_server/render_template.py` 提供 `render_price_res_pic()` / `render_single_cost_pic()`
- 返回临时图片路径，通过 `Image.fromFileSystem()` 发送到 QQ 群

## 并发控制
- 成本计算（`.成本`）使用 `asyncio.Lock` 保证同一时间只有一个计算任务
- 其他指令无并发限制

# 配置要求
- `kahunasystem_host`: kahunabot 后端地址（如 `http://localhost:9527`）
- `cost_username` / `cost_plan`: 成本计算默认用户名和方案名
- 配置由 AstrBot 管理面板注入到 `AstrBotConfig`

# 计划与任务
- 中等及以上复杂度任务需要拆分为 todos
- todo 项要可执行、可验收

# 注意事项
- 新增指令遵循 `@filter.command` 装饰器模式，参考 `main.py` 中已有指令
- 新 API 调用统一封装到 `src/api_client.py`
- 不要绕过 `api_info()` 直接调用后端 API（需要 JWT token）
- 图片渲染失败时降级为纯文本提示，不阻塞响应
