"""统一读取插件配置。

设置界面按功能分组（``seat`` / ``seat_roster`` / ``seat_rename`` / ``seat_kick``），
配置在 AstrBot 里以嵌套 object 保存。早期版本是平铺键
（``seat_rename_enabled`` 这种），这里保留回退读取，避免旧配置直接失效。
"""

from __future__ import annotations


def get(plugin, group: str, key: str, default=None):
    """读取 ``group.key``，找不到时回退旧的 ``group_key`` 平铺写法。"""
    config = getattr(plugin, "config", None)
    if config is None:
        return default

    try:
        nested = config.get(group)
    except Exception:
        nested = None
    if isinstance(nested, dict) and key in nested:
        value = nested.get(key)
        if value is not None and value != "":
            return value

    # 兼容旧版平铺键，例如 seat_rename_enabled / seat_kick_qq_whitelist
    legacy_key = f"{group}_{key}"
    try:
        legacy = config.get(legacy_key)
    except Exception:
        legacy = None
    if legacy is not None and legacy != "":
        return legacy

    return default
