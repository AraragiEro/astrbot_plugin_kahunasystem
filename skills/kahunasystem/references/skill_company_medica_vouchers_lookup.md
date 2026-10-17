# SKILL 内部调用说明：公司医保额度查询

## 适用接口
- `get_company_medica_vouchers`
- 返回核心字段：`data`（二维数组，元素结构为 `[角色名, 额度字符串]`）

## 接口返回格式
```json
{
  "data": [
    ["CrazySheep7", "1,500,000,000"],
    ["Wynn Tim", "1,000,000,000"]
  ]
}
```

## 使用目标
用于成员在群内查询“自己”的医保额度。

## 匹配规则
1. 读取成员群昵称（群名片）。
2. 若昵称包含 `/`，取最后一个 `/` 后的文本作为角色名。
3. 若昵称不包含 `/`，使用整个昵称作为角色名。
4. 角色名与额度清单中的第一列做精确匹配。
5. 匹配成功时返回标准结果；未匹配时返回“未匹配”结果。

## 输出格式
- 匹配成功：
`{群昵称}， 你的{角色名} 角色拥有医保额度： {额度} ISK。`

- 未匹配：
`{群昵称}， 未在医保额度清单中匹配到角色名：{角色名}。`

## 参考伪代码（Python）
```python
def extract_role_name(group_nickname: str) -> str:
    nickname = (group_nickname or "").strip()
    if "/" in nickname:
        # 群名片示例：SETCR-Jack/KomeijiKoishi514
        # 角色名取最后一个 / 后半段
        return nickname.rsplit("/", 1)[-1].strip()
    return nickname


def build_medica_reply(group_nickname: str, api_resp: dict) -> str:
    role_name = extract_role_name(group_nickname)
    rows = (api_resp or {}).get("data") or []

    # 构建角色名 -> 额度映射，兼容异常行结构
    voucher_map = {}
    for row in rows:
        if isinstance(row, (list, tuple)) and len(row) >= 2:
            name = str(row[0]).strip()
            amount = str(row[1]).strip()
            if name:
                voucher_map[name] = amount

    amount = voucher_map.get(role_name)
    if amount:
        return f"{group_nickname}， 你的{role_name} 角色拥有医保额度： {amount} ISK。"
    return f"{group_nickname}， 未在医保额度清单中匹配到角色名：{role_name}。"
```

