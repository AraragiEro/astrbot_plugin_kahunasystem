---
name: zkburl_generator
description: zKillboard API URL 生成器
- 根据用户提供的条件生成 zKillboard API 请求 URL
- 支持多种参数组合（实体筛选、类型筛选、时间筛选等）
- 自动验证 URL 是否符合 API 规范
---

**重要**
url会返回大量数据，不要直接获取url内容，会消耗大量token，。
获取url后使用相关tools进行后续分析。

生成 URL 时必须遵守以下规则：
- URL 必须以斜杠 `/` 结尾
- 如果不传 `/killID/`，必须至少传两个修饰符
- 参数可按任意顺序组合
- 所有 ID 均为 CCP 官方 ID

# 场景路由

## 当用户需要查询击杀记录时

1. 分析用户提供的筛选条件（角色、公司、联盟、舰船类型、星系等）
2. 确定需要的修饰符参数（kills/losses/solo/w-space 等）
3. 构建完整的 API URL
4. 验证 URL 是否符合最少两个修饰符的规则

## URL 构建规则

### 基础 URL
```
https://zkillboard.com/api/
```

### 可选修饰符（按类别）

**1. 分页/时间/数量**
- `/page/#/` - 页码
- `/year/Y/` - 年份（需配合 month）
- `/month/m/` - 月份
- `/pastSeconds/s/` - 过去 X 秒内（最大 604800 = 7天，必须是 3600 的倍数）
- `/killID/#/` - Killmail ID

**2. 类型筛选**
- `/kills/` - 仅击杀（攻击方）
- `/losses/` - 仅损失（受害方）
- `/w-space/` - 仅虫洞空间
- `/solo/` - 仅单人击杀
- `/finalblow-only/` - 仅最后一击
- `/awox/0` 或 `/awox/1` - AWOX 击杀开关
- `/npc/0` 或 `/npc/1` - NPC 击杀开关

**3. 实体筛选**
- `/characterID/#/` - 角色 ID
- `/corporationID/#/` - 公司 ID
- `/allianceID/#/` - 联盟 ID
- `/factionID/#/` - 派系 ID
- `/shipTypeID/#/` - 舰船类型 ID
- `/groupID/#/` - 舰船分组 ID
- `/systemID/#/` - 星系 ID
- `/regionID/#/` - 星域 ID
- `/warID/#/` - 战争 ID
- `/iskValue/#/` - 最小 ISK 价值

### 组合示例

```
# 虫洞击杀（第2页）
https://zkillboard.com/api/kills/w-space/page/2/

# 特定角色的单人击杀
https://zkillboard.com/api/solo/kills/characterID/268946627/

# 虫洞公司损失
https://zkillboard.com/api/w-space/losses/corporationID/98076299/

# 特定星域击杀（最近1小时）
https://zkillboard.com/api/kills/regionID/10000002/pastSeconds/3600/
```

## 输出规范

生成的 URL 应该：
1. 以 `https://zkillboard.com/api/` 开头
2. 以 `/` 结尾
3. 包含至少两个修饰符（如果有 `/killID/` 则除外）
4. 参数顺序不影响结果，但建议按以下顺序组织：
   - 类型筛选 → 实体筛选 → 时间/分页筛选
