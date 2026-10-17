# astrbot_plugin_kahunasystem

EVE Online 市场与工业查询插件，为 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 提供
Jita / FRT 市场比价、制造成本核算、蓝图缺失分析与 zKillboard 链接生成能力。

## 功能

- **市场查价**：查询任意物品在 Jita 或 FRT 联盟市场的价格，返回渲染好的价格图表与买卖深度摘要。
- **制造成本**：按指定方案计算物品的制造成本明细（材料、组件、费用），并渲染为成本图表。
- **账号绑定**：把平台身份与 kahunasystem 账号 / QQ 号关联，供后续按账号取数据。
- **Agent Skills**：随插件提供两个 Skill，让模型自动完成物品名匹配、场景路由与战报链接构造。
- **LLM 工具**：提供 `kahunasystem_get_my_qq`，让模型在调用带 `qq` 参数的后端工具前取到当前用户真实 QQ 号。

## 指令

| 指令 | 说明 | 权限 |
| --- | --- | --- |
| `.ojita <物品> [数量]` | 查询 Jita 市场价格（含图表） | 公开 |
| `.frt <物品> [数量]` | 查询 FRT 联盟市场价格（含图表） | 公开 |
| `.成本 <物品>` | 查询制造成本明细（别名 `cost`） | 公开 |
| `.绑定kahunasystem <uuid>` | 将 QQ 号绑定到 kahunasystem 账号 | 公开 |
| `/绑定QQ <QQ号>` | 绑定当前平台身份与 QQ 号（QQ 官方机器人必做一次） | 公开 |
| `/解绑QQ` | 解除当前身份的 QQ 号绑定 | 公开 |
| `/我的绑定` | 查看当前身份的绑定情况 | 公开 |
| `/绑定审计 <QQ号>` | 查看某 QQ 号的绑定 / 解绑审计 | 管理员 |

> 指令前缀由 AstrBot 的 `wake_prefix` 决定（默认 `/`），上表沿用习惯写法，实际以你的配置为准。

## 前置要求

本插件是**客户端**，本身不含市场数据，需要通过 HTTP 调用后端
[EveKahunaSystem](https://github.com/AraragiEro/EveKahunaSystem)：

```
QQ 群指令 → astrbot_plugin_kahunasystem → HTTP → EveKahunaSystem（Quart 后端）→ PostgreSQL / Neo4j / SDE
```

因此使用前需要：

1. 自行部署 EveKahunaSystem 后端；
2. 在后端 `config.toml` 的 `[SERVICE]` 中设置 `API_KEY`；
3. 在本插件配置中填入后端地址与相同的服务密钥。

未配置后端时，指令会返回「XX 暂时不可用，请稍后再试」，详细原因见 AstrBot 日志。

## 配置

插件配置在 AstrBot WebUI 的 `插件 → astrbot_plugin_kahunasystem → 配置` 中填写，
定义见 [`_conf_schema.json`](./_conf_schema.json)：

| 配置项 | 说明 |
| --- | --- |
| `kahunasystem_host` | 后端根地址，含协议与端口，例如 `http://127.0.0.1:9527` |
| `service_api_key` | 服务间认证密钥，需与后端 `[SERVICE] API_KEY` 完全一致（界面中以密码框遮罩显示） |
| `cost_username` | 成本计算的默认用户名 |
| `cost_plan` | 成本计算的默认方案名 |
| `bind_allow_group_ids` | 允许执行绑定的群号，逗号分隔；留空表示不限制 |
| `vip_white_session_ids` | 跳过 VIP 校验的会话标识列表 |

## 安装

在 AstrBot WebUI 的 `插件` 页搜索本插件名安装，或：

```bash
cd AstrBot/data/plugins
git clone https://github.com/AraragiEro/astrbot_plugin_kahunasystem
```

依赖仅 `jinja2`，AstrBot 核心环境已自带；插件出图优先使用 AstrBot 内置的文转图服务
（`t2i_endpoint`），不额外引入浏览器依赖。

## 平台支持

| 平台 | 说明 |
| --- | --- |
| `aiocqhttp`（OneBot v11） | 完整支持 |
| `qq_official` / `qq_official_webhook` | 支持；官方机器人拿不到 QQ 号，需先执行一次 `/绑定QQ <QQ号>` |

QQ 官方机器人的富媒体（图片）消息不解析 @ 标签，因此出图指令会先单独发送一条
「@ 发送者 + 文本摘要」，再发送图片；这是刻意行为，不是重复发送。

## 数据与隐私

- 平台身份与 QQ 号的映射存放在 AstrBot 插件 KV 存储（`AstrBot/data/data_v4.db`），插件更新不会丢失；
- 一个 QQ 号只能绑定一个平台身份，换号需先解绑；
- 群聊与单聊的身份标识不同，需分别在各自场景绑定；
- 指令失败时群里只回固定文案，状态码与响应体仅写入 AstrBot 日志，不外发。

## 变更记录

见 [CHANGELOG.md](./CHANGELOG.md)。

## 许可证

[GNU Affero General Public License v3.0](./LICENSE)
