"""平台身份 ↔ 真实 QQ 号 的映射层（中间层）。

背景
----
QQ 官方机器人只提供 openid（群聊 ``member_openid`` / 单聊 ``user_openid``，
都是 32 位 hex），平台不提供 QQ 号，也没有反查接口；而 kahunabot 后端与既有数据
全部以整数 QQ 号作为业务身份（``user_qq_binding``、资产归属、权限、缓存 key 等）。

做法
----
在插件侧维护 openid → QQ 号映射，业务需要用 QQ 号时统一调用 :func:`resolve_qq`：

- 数字身份（OneBot/aiocqhttp 等平台）→ 直接当 QQ 号使用，不需要映射；
- 非数字身份（QQ 官方 openid）→ 查映射表，缺失时由调用方提示用户 ``/绑定QQ``。

存储
----
使用 AstrBot 插件自带的 KV 存储（``Star.put_kv_data`` / ``get_kv_data``），
底层落在 ``AstrBot/data/data_v4.db``（SQLite）：与插件代码目录分离，
插件被面板更新覆盖时映射不会丢，且读写与并发由框架负责。

键结构（一条映射两个键，避免整块读改写带来的竞态）
--------------------------------------------------
- ``uid2qq:<platform>:<scene>:<uid>`` → 记录 dict（含 qq 与审计字段）
- ``qq2uid:<qq>`` → 该 QQ 号所属的平台身份键（用于唯一性校验）
- ``audit:<qq>`` → 绑定/解绑审计记录（最多保留 20 条）

注意
----
群聊与单聊的 openid 不是同一个值，因此映射按场景分开登记：
同一个人的「群身份」和「私聊身份」需要各自绑定一次。
若后续实测发现同一用户在不同群的 ``member_openid`` 并不一致，
需要把群 ID 也并入 :meth:`IdentityStore.identity_key`。
"""

from __future__ import annotations

from datetime import datetime

from astrbot.core import logger

# 官方机器人平台标识（与 src/event.py 保持一致）
QQ_OFFICIAL_PLATFORMS = ("qq_official", "qq_official_webhook")

UID_TO_QQ_PREFIX = "uid2qq"
QQ_TO_UID_PREFIX = "qq2uid"
AUDIT_PREFIX = "audit"
AUDIT_MAX_ENTRIES = 20

_store: "IdentityStore | None" = None


def set_identity_store(store: "IdentityStore | None") -> None:
    """由 main.py 在插件初始化时注入（传入 Star 实例，它自带 KV 方法）。"""
    global _store
    _store = store


def get_identity_store() -> "IdentityStore | None":
    return _store


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


async def resolve_qq(event) -> int | None:
    """把当前事件的发送者解析成真实 QQ 号；解析不出来返回 None。

    - 发送者 id 是纯数字（OneBot 等平台）→ 直接返回，不需要映射；
    - 否则（QQ 官方 openid）→ 查映射表。
    """
    sender_id = str(event.get_sender_id() or "").strip()
    if sender_id.isdigit():
        return int(sender_id)

    store = _store
    if store is None:
        logger.warning("身份映射层未初始化，无法解析 QQ 号")
        return None
    return await store.get_qq(event)


class IdentityStore:
    """openid → QQ 号映射表的读写封装。"""

    def __init__(self, kv_owner) -> None:
        # kv_owner 通常是插件的 Star 实例：提供 get_kv_data / put_kv_data / delete_kv_data
        self._kv = kv_owner

    # ------------------------------------------------------------------ 键

    @staticmethod
    def identity_key(event) -> str:
        """平台身份键：``<platform>:<scene>:<uid>``。"""
        scene = "private" if event.is_private_chat() else "group"
        return f"{event.get_platform_name()}:{scene}:{event.get_sender_id()}"

    @staticmethod
    def _uid_key(identity: str) -> str:
        return f"{UID_TO_QQ_PREFIX}:{identity}"

    @staticmethod
    def _qq_key(qq: int) -> str:
        return f"{QQ_TO_UID_PREFIX}:{qq}"

    @staticmethod
    def _audit_key(qq: int) -> str:
        return f"{AUDIT_PREFIX}:{qq}"

    # ------------------------------------------------------------------ 读

    async def get_binding(self, event) -> dict | None:
        """返回当前平台身份的绑定记录（dict），未绑定返回 None。"""
        return await self._kv.get_kv_data(
            self._uid_key(self.identity_key(event)), None
        )

    async def get_qq(self, event) -> int | None:
        """返回当前平台身份绑定的 QQ 号，未绑定返回 None。"""
        binding = await self.get_binding(event)
        if not binding:
            return None
        try:
            return int(binding.get("qq"))
        except (TypeError, ValueError):
            logger.warning(f"绑定记录中的 QQ 号异常: {binding}")
            return None

    # ------------------------------------------------------------------ 写

    async def bind(self, event, qq: int) -> tuple[bool, str]:
        """绑定「当前平台身份 → QQ 号」，返回 (是否成功, 给用户的提示)。

        唯一性规则（基础防冒绑）：
        - 一个 QQ 号只能属于一个平台身份；
        - 一个平台身份同时只能绑定一个 QQ 号，换号需先解绑。
        """
        identity = self.identity_key(event)
        uid_key = self._uid_key(identity)
        qq_key = self._qq_key(qq)

        owner = await self._kv.get_kv_data(qq_key, None)
        if owner and owner != identity:
            logger.warning(f"[身份绑定] 拒绝：QQ {qq} 已绑定到 {owner}，本次来自 {identity}")
            return False, f"QQ {qq} 已经绑定过其他身份了，如需更换请先让对方解绑或联系管理员。"

        current = await self._kv.get_kv_data(uid_key, None)
        if current and str(current.get("qq")) != str(qq):
            return (
                False,
                f"当前身份已绑定 QQ {current.get('qq')}，请先发送解绑指令再绑定新号码。",
            )

        scene = "private" if event.is_private_chat() else "group"
        record = {
            "qq": qq,
            "platform": event.get_platform_name(),
            "scene": scene,
            "uid": str(event.get_sender_id() or ""),
            "group_id": str(event.get_group_id() or ""),
            "sender_name": str(event.get_sender_name() or ""),
            "bound_at": _now(),
        }
        await self._kv.put_kv_data(uid_key, record)
        await self._kv.put_kv_data(qq_key, identity)
        await self._append_audit(qq, {"action": "bind", **record})
        logger.info(
            f"[身份绑定] {identity} -> QQ {qq}"
            f"（群 {record['group_id'] or '-'}，昵称 {record['sender_name'] or '-'}）"
        )
        return True, f"绑定成功：当前身份已关联 QQ {qq}"

    async def unbind(self, event) -> tuple[bool, str]:
        """解绑当前平台身份，返回 (是否成功, 给用户的提示)。"""
        identity = self.identity_key(event)
        uid_key = self._uid_key(identity)
        record = await self._kv.get_kv_data(uid_key, None)
        if not record:
            return False, "当前身份还没有绑定 QQ 号。"

        qq = record.get("qq")
        await self._kv.delete_kv_data(uid_key)
        if qq is not None:
            await self._kv.delete_kv_data(self._qq_key(int(qq)))
            await self._append_audit(
                int(qq),
                {
                    "action": "unbind",
                    "qq": int(qq),
                    "platform": record.get("platform", ""),
                    "scene": record.get("scene", ""),
                    "uid": record.get("uid", ""),
                    "group_id": record.get("group_id", ""),
                    "sender_name": record.get("sender_name", ""),
                    "bound_at": _now(),
                },
            )
        logger.info(f"[身份绑定] 解绑 {identity}（原 QQ {qq}）")
        return True, f"已解绑 QQ {qq}"

    async def _append_audit(self, qq: int, entry: dict) -> None:
        """追加一条审计记录（最多保留最近 AUDIT_MAX_ENTRIES 条）。"""
        key = self._audit_key(qq)
        try:
            history = await self._kv.get_kv_data(key, []) or []
            if not isinstance(history, list):
                history = []
            history.append(entry)
            await self._kv.put_kv_data(key, history[-AUDIT_MAX_ENTRIES:])
        except Exception as e:  # 审计失败不影响主流程
            logger.warning(f"写入绑定审计失败: {e}")

    async def get_audit(self, qq: int) -> list:
        try:
            history = await self._kv.get_kv_data(self._audit_key(qq), []) or []
            return history if isinstance(history, list) else []
        except Exception as e:
            logger.warning(f"读取绑定审计失败: {e}")
            return []

    # -------------------------------------------------------------- 展示用

    async def describe(self, event) -> str:
        """给 /我的绑定 用的可读信息。

        会打印当前平台身份键（含 uid 与群号），方便核对「同一个用户在不同群的
        member_openid 是否一致」。
        """
        identity = self.identity_key(event)
        binding = await self.get_binding(event)
        lines = [f"平台身份：{identity}"]
        if not binding:
            lines.append("绑定状态：未绑定 QQ 号")
            return "\n".join(lines)
        lines.append(f"绑定状态：QQ {binding.get('qq')}")
        lines.append(f"绑定时间：{binding.get('bound_at', '-')}")
        if binding.get("group_id"):
            lines.append(f"绑定场景：{binding.get('scene')}（群 {binding.get('group_id')}）")
        else:
            lines.append(f"绑定场景：{binding.get('scene')}")
        return "\n".join(lines)
