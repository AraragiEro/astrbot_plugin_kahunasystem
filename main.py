import asyncio

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api import AstrBotConfig

from .src.event import Event, set_command_prefix
from .src.api_client import set_service_api_key
from .src.identity import IdentityStore, set_identity_store
from .src.picture_render_server.picture_render import set_t2i_renderer
from .src.seat_auth import PLUGIN_NAME as SEAT_PLUGIN_NAME, handle_validate_request
from .src.seat_roster import refresh_roster
from .src.seat_rename import configured_group_ids, sync_all_groups
from .src.seat_kick import configured_kick_group_ids, kick_all_groups
from .src.seat_config import get as seat_cfg
from .src.seat_join import handle_group_request


@register(
    "astrbot_plugin_kahunasystem",
    "AraragiEro",
    "EVE Online 市场查价、制造成本与 zKill 战报查询",
    "0.0.10",
)
class MyPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        Event.config = self.config
        # 服务间认证：QQ 机器人无法交互式登录，改用共享服务密钥（config.toml [SERVICE] API_KEY）
        set_service_api_key(config.get("service_api_key", ""))
        # MCP 中间件已接管所有功能工具，插件只保留指令交互

        # 图片渲染：优先走 AstrBot 内置的文转图服务（远程渲染）。
        # 这样插件本地不需要 pyppeteer —— 它依赖 websockets<11，与 AstrBot 核心的
        # websockets>=15 冲突，装了会被「核心依赖版本保护」拦截。
        try:
            global_config = context.get_config() or {}
        except Exception as e:
            logger.warning(f"读取 AstrBot 全局配置失败，文转图将使用默认端点: {e}")
            global_config = {}
        set_t2i_renderer(
            getattr(context, "html_renderer", None),
            global_config.get("t2i_endpoint"),
        )

        # QQ 官方机器人的图片消息（富媒体）承载不了 @，会原样显示 <qqbot-at-user id="..." />。
        # 插件已对出图指令改为「先单发一条 @ 摘要、再发图」，这里只做提示。
        platform_settings = global_config.get("platform_settings") or {}
        if platform_settings.get("reply_with_mention"):
            logger.info(
                "检测到「回复时 @ 用户」已开启：QQ 官方机器人的图片消息无法承载 @，"
                "出图指令会单独发送 @ 摘要消息（图片消息已跳过该装饰）。"
            )

        # 平台身份 → 真实 QQ 号 的映射层。
        # QQ 官方机器人只给 openid，而 kahunabot 后端与既有数据都以 QQ 号为业务身份，
        # 这里用插件自带的 KV 存储（落在 AstrBot/data/data_v4.db，插件更新不会丢）维护映射。
        set_identity_store(IdentityStore(self))

        # 提示语里要显示正确的指令前缀（AstrBot 的 wake_prefix，默认 "/"）
        wake_prefix = global_config.get("wake_prefix") or ["/"]
        if isinstance(wake_prefix, (list, tuple)):
            command_prefix = wake_prefix[0] if wake_prefix else "/"
        else:
            command_prefix = str(wake_prefix)
        set_command_prefix(command_prefix)

        # 可视化视图（views/seat-auth/）的后端接口：校验并保存 SeAT 登录态。
        # 浏览器页面无法读取 seat.winterco.org 的 httpOnly Cookie，因此由用户
        # 手动粘贴、插件后端代为请求一次校验后再落库。
        context.register_web_api(
            f"/{SEAT_PLUGIN_NAME}/seat/validate",
            self.seat_validate,
            ["POST"],
            "校验并保存 SeAT 登录态",
        )

    async def initialize(self):
        """插件加载后启动 SeAT 的三个后台任务：名单刷新、名片同步、成员清理。"""
        self._seat_tasks = [
            asyncio.create_task(self._seat_refresh_loop()),
            asyncio.create_task(self._seat_rename_loop()),
            asyncio.create_task(self._seat_kick_loop()),
        ]

    def _interval_seconds(
        self, group: str, key: str = "interval_minutes", default: int = 60
    ) -> int:
        """读取周期（分钟）并换算成秒，最小 5 分钟，防止误配成 0。"""
        try:
            minutes = int(seat_cfg(self, group, key, default))
        except (TypeError, ValueError):
            minutes = default
        return max(5, minutes) * 60

    async def _seat_refresh_loop(self):
        """按周期刷新公司成员 QQ 名单。"""
        while True:
            try:
                await refresh_roster(self)
            except Exception as exc:
                logger.warning(f"SeAT 名单刷新失败：{exc}")
            await asyncio.sleep(
                self._interval_seconds("seat_roster", "refresh_interval_minutes")
            )

    async def _seat_rename_loop(self):
        """按周期同步群名片；首次延迟一会儿，等名单先刷出来。"""
        await asyncio.sleep(30)
        while True:
            try:
                enabled = bool(seat_cfg(self, "seat_rename", "enabled", True))
                if enabled and configured_group_ids(self):
                    await sync_all_groups(self)
            except Exception as exc:
                logger.warning(f"SeAT 名片同步失败：{exc}")
            await asyncio.sleep(self._interval_seconds("seat_rename"))

    async def _seat_kick_loop(self):
        """按周期清理不在名单里的群成员；首次稍晚启动。"""
        await asyncio.sleep(60)
        while True:
            try:
                enabled = bool(seat_cfg(self, "seat_kick", "enabled", True))
                if enabled and configured_kick_group_ids(self):
                    await kick_all_groups(self)
            except Exception as exc:
                logger.warning(f"SeAT 成员清理失败：{exc}")
            await asyncio.sleep(self._interval_seconds("seat_kick"))

    @filter.command("helloworld")
    async def helloworld(self, event: AstrMessageEvent):
        """这是一个 hello world 指令。"""
        user_name = event.get_sender_name()
        message_str = event.message_str
        logger.info(event.get_messages())
        yield event.plain_result(f"Hello, {user_name}, 你发了 {message_str}!")

    @filter.command("ojita")
    async def cost(self, event: AstrMessageEvent, require_str: str):
        """查询 jita 市场价格。"""
        yield await Event.oprice(event, require_str)

    @filter.command("frt")
    async def frtprice(self, event: AstrMessageEvent, require_str: str):
        """查询 FRT 市场价格。"""
        yield await Event.frt_price(event, require_str)

    @filter.command("成本", alias={"cost"})
    async def costdetail(self, event: AstrMessageEvent, product: str, username: str = None, plan_name: str = None):
        """查询成本详情。"""
        yield await Event.costdetail(event, product, username, plan_name)

    @filter.command("绑定kahunasystem")
    async def bind_kahunasystem(self, event: AstrMessageEvent):
        message_str = event.get_message_str().strip()
        parts = message_str.split()
        uuid = parts[1] if len(parts) >= 2 else None
        yield await Event.bind_kahunasystem(event, uuid)

    # ---- 平台身份 ↔ QQ 号 映射（QQ 官方机器人只给 openid，业务仍需要 QQ 号）----

    @filter.command("绑定QQ", alias={"绑定qq", "bindqq"})
    async def bind_qq(self, event: AstrMessageEvent, qq_str: str = ""):
        """绑定你的 QQ 号（QQ 官方机器人拿不到 QQ 号，需要自报一次）。"""
        yield await Event.bind_qq(event, qq_str)

    @filter.command("解绑QQ", alias={"解绑qq", "unbindqq"})
    async def unbind_qq(self, event: AstrMessageEvent):
        """解除当前身份的 QQ 号绑定。"""
        yield await Event.unbind_qq(event)

    @filter.command("我的绑定", alias={"mybind"})
    async def my_binding(self, event: AstrMessageEvent):
        """查看当前身份的绑定情况。"""
        yield await Event.my_binding(event)

    @filter.command("绑定审计", alias={"bindaudit"})
    async def binding_audit(self, event: AstrMessageEvent, qq_str: str = ""):
        """查看某个 QQ 号的绑定审计（仅管理员）。"""
        yield await Event.binding_audit(event, qq_str)

    # ---- 给 LLM 的映射查询工具（供调用 kahunabot 的 MCP 工具前取真实 QQ 号）----

    @filter.llm_tool(name="kahunasystem_get_my_qq")
    async def get_my_qq(self, event: AstrMessageEvent) -> str:
        """查询当前对话用户的真实 QQ 号（kahunasystem 身份）。

        只要需要调用带 qq 参数的 kahunasystem 工具（市场查价、制造成本、VIP 状态、
        关注列表、资产等），都必须先调用本工具拿到当前用户的真实 QQ 号，
        再把返回值中的号码作为 qq 参数传入；不要自行猜测 QQ 号，也不要使用其他用户的 QQ 号。
        返回内容若提示无法获取，请按其中的指引转告用户，不要继续调用需要 qq 参数的工具。
        """
        return await Event.get_my_qq_for_llm(event)

    @filter.command("ssid")
    async def ssid(self, event: AstrMessageEvent):
        yield event.plain_result(f"你的 session id 是: {event.get_session_id()}")

    @filter.command("seat刷新", alias={"刷新QQ", "seatrefresh"})
    async def seat_refresh(self, event: AstrMessageEvent):
        """立即刷新公司成员 QQ 名单。"""
        try:
            data = await refresh_roster(self)
        except Exception as exc:
            yield event.plain_result(f"刷新失败：{exc}")
            return
        missing = data["total"] - data["with_qq"]
        yield event.plain_result(
            f"SeAT 名单已刷新：共 {data['total']} 人，"
            f"{data['with_qq']} 人填了 QQ，{missing} 人未填。"
        )

    @filter.command("seat改名", alias={"同步名片", "seatrename"})
    async def seat_rename(self, event: AstrMessageEvent):
        """立即按名单同步一次群名片。"""
        try:
            results = await sync_all_groups(self)
        except Exception as exc:
            yield event.plain_result(f"同步失败：{exc}")
            return
        totals = {
            key: sum(item.get(key, 0) for item in results)
            for key in ("checked", "matched", "changed", "planned", "failed", "limited")
        }
        debug_mode = any(item.get("debug") for item in results)
        prefix = "（调试模式，未实际改名）" if debug_mode else ""
        yield event.plain_result(
            f"群名片同步完成{prefix}（{len(results)} 个群）：检查 {totals['checked']} 人，"
            f"已符合 {totals['matched']}，改名 {totals['changed']}，"
            f"预计改名 {totals['planned']}，失败 {totals['failed']}，"
            f"因限额跳过 {totals['limited']}。"
        )

    @filter.command("seat踢人", alias={"清理成员", "seatkick"})
    async def seat_kick(self, event: AstrMessageEvent):
        """立即清理不在名单里的群成员。"""
        try:
            results = await kick_all_groups(self)
        except Exception as exc:
            yield event.plain_result(f"清理失败：{exc}")
            return
        totals = {
            key: sum(item.get(key, 0) for item in results)
            for key in (
                "total",
                "kept",
                "kicked",
                "planned",
                "failed",
                "skipped_role",
                "skipped_whitelist",
                "limited",
            )
        }
        debug_mode = any(item.get("debug") for item in results)
        prefix = "（调试模式，未实际踢人）" if debug_mode else ""
        yield event.plain_result(
            f"群成员清理完成{prefix}（{len(results)} 个群）：群内 {totals['total']} 人，"
            f"保留 {totals['kept']}，踢出 {totals['kicked']}，"
            f"预计踢出 {totals['planned']}，失败 {totals['failed']}，"
            f"管理员跳过 {totals['skipped_role']}，白名单跳过 {totals['skipped_whitelist']}，"
            f"因限额跳过 {totals['limited']}。"
        )

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_group_request(self, event: AstrMessageEvent):
        """入群申请自动审核（仅 OneBot 群请求，见 src/seat_join.py）。"""
        raw = getattr(getattr(event, "message_obj", None), "raw_message", None)
        if raw is None:
            return
        await handle_group_request(self, raw)

    async def seat_validate(self):
        """可视化视图「验证并保存」的后端入口（见 views/seat-auth/）。"""
        return await handle_validate_request(self)

    async def terminate(self):
        """卸载插件时取消全部后台任务。"""
        for task in getattr(self, "_seat_tasks", []) or []:
            if task is not None and not task.done():
                task.cancel()
