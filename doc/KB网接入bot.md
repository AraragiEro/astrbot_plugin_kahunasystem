---
创建日期: 2026-03-03
修改日期: 2026-03-10
tags:
  - 游戏
  - 游戏/EVE
  - 游戏/Bot开发
---

# zKillboard API (Killmails)

> 来源：[API (Killmails) - GitHub Wiki](https://github.com/zKillboard/zKillboard/wiki/API-(Killmails))

---

## 基础 URL

```
https://zkillboard.com/api/
```

---

## 重要使用规范

| 规则 | 说明 |
|------|------|
| **单次最大数量** | 每次请求最多 200 条 killmail |
| **ID 类型** | 所有 ID 均为 CCP 官方 ID |
| **必须以 `/` 结尾** | URL 必须以斜杠结尾，否则请求会静默失败 |
| **最少两个修饰符** | 如果不传 `/killID/`，必须至少传两个修饰符（如 `/w-space/`、`/solo/` 或任意 `/xID/`） |
| **默认格式** | JSON，已启用 CORS |
| **默认排序** | 降序（最新到最旧） |
| **默认页码** | 1 |

---

## 修饰符参数

### 1. 分页/时间/数量

| 参数 | 说明 | 示例 |
|------|------|------|
| `/page/#/` | 页码 | `/page/2/` |
| `/year/Y/` | 年份（需配合 month） | `/year/2024/` |
| `/month/m/` | 月份 | `/month/01/` |
| `/pastSeconds/s/` | 过去 X 秒内的击杀（最大 604800 = 7天，必须是 3600 的倍数） | `/pastSeconds/3600/` |
| `/killID/#/` | Killmail ID | `/killID/12345678/` |

### 2. 类型筛选

| 参数                    | 说明                                   |
| --------------------- | ------------------------------------ |
| `/kills/`             | 仅击杀（攻击方）                             |
| `/losses/`            | 仅损失（受害方）                             |
| `/w-space/`           | 仅虫洞空间 - 可与 `/kills/` 或 `/losses/` 组合 |
| `/solo/`              | 仅单人击杀 - 可与 `/kills/` 或 `/losses/` 组合 |
| `/finalblow-only/`    | 仅最后一击                                |
| `/awox/0` 或 `/awox/1` | AWOX 击杀开关                            |
| `/npc/0` 或 `/npc/1`   | NPC 击杀开关                             |

### 3. 实体筛选

| 参数 | 说明 |
|------|------|
| `/characterID/#/` | 角色 ID |
| `/corporationID/#/` | 公司 ID |
| `/allianceID/#/` | 联盟 ID |
| `/factionID/#/` | 派系 ID |
| `/shipTypeID/#/` | 舰船类型 ID |
| `/groupID/#/` | 舰船分组 ID |
| `/systemID/#/` | 星系 ID |
| `/regionID/#/` | 星域 ID |
| `/warID/#/` | 战争 ID |
| `/iskValue/#/` | 最小 ISK 价值（返回大于等于该值的击杀） |

### 4. 信息控制

| 参数 | 说明 |
|------|------|
| `/zkbOnly/` | 返回精简数据：killID、hash、points、value（2018-10-02 后已默认应用于所有请求） |

---

## 请求示例

```bash
# 所有虫洞击杀
https://zkillboard.com/api/kills/w-space/

# 单人损失
https://zkillboard.com/api/losses/solo/

# 特定角色的击杀
https://zkillboard.com/api/kills/characterID/268946627/

# 特定角色的单人击杀
https://zkillboard.com/api/solo/kills/characterID/268946627/

# 虫洞公司损失（第2页）
https://zkillboard.com/api/w-space/corporationID/98076299/page/2/

# 特定星域的击杀
https://zkillboard.com/api/kills/regionID/10000002/

# 组合参数（虫洞+单人+角色）
https://zkillboard.com/api/w-space/solo/characterID/123456789/
```

---

## 响应格式

API 返回 JSON 格式的 killmail 数据，每条包含：

- 标准 CCP killmail 数据（受害者、攻击者、物品等）
- **zkb 块**：zKillboard 特定元数据
  - `killID` 和 `hash`
  - `points`（zKill 积分）
  - `value`（ISK 价值）
  - 其他 zKill 元数据

---

## 已废弃功能

以下功能因滥用或系统变更已移除：

- `/xml/` - 仅支持 JSON
- `zkbOnly`、`no-attackers`、`no-items`、`asc`、`desc`、`json` - 将返回非 JSON 错误
- 逗号分隔的多实体获取（如 `characterID/123,456/`）
- 基于时间的排序参数
- `/limit/#/` - 请使用分页
- `/startTime/YmdHi/` 和 `/endTime/YmdHi/`
- 手动 killmail 筛选

---

## 其他数据来源

| 来源 | 说明 |
|------|------|
| **WebSocket API** | 实时 killstream |
| **RedisQ** | 推送完整 killmail 的端点 |
| **EVE Ref** | `data.everef.net/killmails/`（zKillboard 数据的每日归档） |

---

## 开发建议

1. **自由组合修饰符**：所有修饰符可按任意顺序组合
2. **同时获取击杀和损失**：不传 `/kills/` 或 `/losses/` 即可
3. **本地缓存**：建议本地缓存以减少 API 负载
4. **遵守速率限制**：连续 25 次请求将触发抓取限制

---

## Bot 接入规划

待补充...
