import asyncio
import json
import os

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api import AstrBotConfig

from .src.event import Event
from .src.api_client import set_service_api_key
from .src.picture_render_server.picture_render import set_t2i_renderer
from .src.seat_auth import PLUGIN_NAME as SEAT_PLUGIN_NAME, handle_validate_request
from .src.seat_roster import refresh_roster
from .src.seat_rename import configured_group_ids, sync_all_groups
from .src.seat_kick import configured_kick_group_ids, kick_all_groups
from .src.seat_config import get as seat_cfg
from .src.seat_join import handle_group_request
from .src.bank import handlers as bank_handlers
from .src.bank.runtime import BankRuntime

PLUGIN_REGISTER_NAME = "astrbot_plugin_kahunasystem"


def _resolve_bank_data_dir() -> str:
    """成员银行账本的持久化目录（插件更新不会丢的那个卷）。"""
    try:
        from astrbot.core.star.star_tools import StarTools

        return str(StarTools.get_data_dir(PLUGIN_REGISTER_NAME))
    except Exception as exc:  # 极端情况下退回到插件目录
        logger.warning(f"获取插件数据目录失败，成员银行改存插件目录：{exc}")
        return os.path.join(os.path.dirname(__file__), "data", "bank")


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

        # 业务身份直接用事件发送者 id（OneBot/aiocqhttp 等平台即真实 QQ 号）：
        # openid ↔ QQ 号的映射层已回退移除，不再维护绑定表。
        # 可视化视图（views/seat-auth/）的后端接口：校验并保存 SeAT 登录态。
        # 浏览器页面无法读取 seat.winterco.org 的 httpOnly Cookie，因此由用户
        # 手动粘贴、插件后端代为请求一次校验后再落库。
        context.register_web_api(
            f"/{SEAT_PLUGIN_NAME}/seat/validate",
            self.seat_validate,
            ["POST"],
            "校验并保存 SeAT 登录态",
        )

        # ---- 成员银行账户（PAP 结算）----
        # 账本落在 StarTools 数据目录（挂载卷），插件更新不会丢
        self.bank_runtime = BankRuntime(_resolve_bank_data_dir())
        self._bank_admins = self._load_bank_admins()
        # 可视化视图（views/bank/）的后端：单入口按 action 分发
        context.register_web_api(
            f"/{SEAT_PLUGIN_NAME}/bank",
            self.bank_api,
            ["POST"],
            "成员银行管理接口",
        )

    # ---- 成员银行：公共小工具 ----

    def _load_bank_admins(self) -> set[str]:
        """管理员 QQ 白名单（配置 bank.admin_qqs，英文逗号分隔）。"""
        raw = seat_cfg(self, "bank", "admin_qqs", "") or ""
        if isinstance(raw, (list, tuple)):
            return {str(x).strip() for x in raw if str(x).strip()}
        return {x.strip() for x in str(raw).replace("，", ",").split(",") if x.strip()}

    @staticmethod
    def _sender_qq(event: AstrMessageEvent) -> str:
        """取发送者 QQ；非数字身份（如 QQ 官方机器人 openid）返回空串。"""
        try:
            sender = str(event.get_sender_id() or "").strip()
        except Exception:
            return ""
        return sender if sender.isdigit() else ""

    def _bank_ok(self, event: AstrMessageEvent) -> tuple[str, str]:
        """返回 ``(qq, 错误文案)``，QQ 为空时由调用方直接回错误文案。"""
        qq = self._sender_qq(event)
        if not qq:
            return "", "无法识别你的 QQ 号，成员银行功能暂时不可用。"
        return qq, ""

    def _require_admin(self, event: AstrMessageEvent) -> tuple[str, str]:
        qq, err = self._bank_ok(event)
        if err:
            return "", err
        if not bank_handlers.is_admin(qq, self._bank_admins):
            return "", "这条指令只有管理员能用。"
        return qq, ""

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

    # ---- 给 LLM 的取号工具（供调用 kahunabot 的 MCP 工具前取真实 QQ 号）----

    @filter.llm_tool(name="kahunasystem_get_my_qq")
    async def get_my_qq(self, event: AstrMessageEvent) -> str:
        """查询当前对话用户的真实 QQ 号（kahunasystem 身份）。

        只要需要调用带 qq 参数的 kahunasystem 工具（市场查价、制造成本、VIP 状态、
        关注列表、资产等），都必须先调用本工具拿到当前用户的真实 QQ 号，
        再把返回值中的号码作为 qq 参数传入；不要自行猜测 QQ 号，也不要使用其他用户的 QQ 号。
        返回内容若提示无法获取，请按其中的指引转告用户，不要继续调用需要 qq 参数的工具。
        """
        return await Event.get_my_qq_for_llm(event)

    # ---- 成员银行：给 LLM 的工具 ----

    def _bank_character_hint(self, qq: str) -> str:
        """从名单缓存里按 QQ 反查角色名（拿不到就返回空串，不影响记账）。"""
        try:
            roster = self._bank_roster_cache or {}
        except AttributeError:
            return ""
        for item in roster.get("items") or []:
            if str(item.get("qq_id") or "") == str(qq):
                return str(item.get("character") or "")
        return ""

    async def _bank_roster(self) -> dict:
        """惰性读取名单缓存（用于下单时补角色名）。"""
        if getattr(self, "_bank_roster_cache", None) is None:
            try:
                from .src.seat_roster import load_roster

                self._bank_roster_cache = await load_roster(self) or {}
            except Exception as exc:
                logger.warning(f"读取 SeAT 名单失败（不影响下单）：{exc}")
                self._bank_roster_cache = {}
        return self._bank_roster_cache

    # ---- 成员银行：Web 视图后端（分发实现在 src/bank/webapi.py）----

    async def bank_api(self):
        """成员银行 Web 视图的唯一后端入口。"""
        from astrbot.api.web import json_response, request

        from .src.bank.webapi import dispatch

        payload = await request.json(default={}) or {}
        action = str(payload.get("action") or "").strip()
        try:
            result = await dispatch(self, action, payload)
        except Exception as exc:
            logger.error(f"成员银行 Web 接口失败（{action}）：{exc}")
            result = {"ok": False, "message": f"操作失败：{exc}"}
        return json_response(result)

    @filter.llm_tool(name="bank_my_account")
    async def bank_my_account(self, event: AstrMessageEvent) -> str:
        """查询当前用户自己的成员银行账户。

        返回余额、额外津贴、当月奖励、三桶合计与最近流水。
        只能查当前会话发送者本人，不接受指定他人的参数。
        用户问「我还有多少额度 / 余额多少 / 这个月发了多少奖金」时调用。
        """
        qq, err = self._bank_ok(event)
        if err:
            return err
        data = await self.bank_runtime.run(bank_handlers.my_account, qq)
        return json.dumps(data, ensure_ascii=False)

    @filter.llm_tool(name="bank_create_order")
    async def bank_create_order(
        self, event: AstrMessageEvent, item: str, quantity: int
    ) -> str:
        """提交一个消费订单，等待管理员确认额度后再扣款。

        参数：
        - item：物品名称（字符串）
        - quantity：数量（正整数）

        用户说「我要下单」「我要买 X 个 Y」时调用。
        不要询问或填写价格——金额一律由管理员确认。
        也不要在下单前判断用户余额是否够，那是管理员确认时的步骤。
        """
        qq, err = self._bank_ok(event)
        if err:
            return err
        try:
            qty = int(quantity)
        except (TypeError, ValueError):
            return "数量必须是正整数。"
        await self._bank_roster()
        character = self._bank_character_hint(qq)
        data = await self.bank_runtime.run(
            bank_handlers.place_order, qq, character, str(item or ""), qty)
        return json.dumps(data, ensure_ascii=False)

    @filter.llm_tool(name="bank_my_orders")
    async def bank_my_orders(self, event: AstrMessageEvent) -> str:
        """查看当前用户自己最近的消费订单及处理状态。

        用户问「我下的单怎么样了」「我的订单」时调用。
        """
        qq, err = self._bank_ok(event)
        if err:
            return err
        data = await self.bank_runtime.run(bank_handlers.my_orders, qq)
        return json.dumps(data, ensure_ascii=False)

    @filter.llm_tool(name="bank_admin_pending_orders")
    async def bank_admin_pending_orders(self, event: AstrMessageEvent) -> str:
        """[管理员] 列出所有待确认的消费订单。

        返回订单号、下单人 QQ、角色名、物品、数量、提交时间。
        管理员要处理订单时先用它看清单。
        """
        qq, err = self._require_admin(event)
        if err:
            return err
        data = await self.bank_runtime.run(bank_handlers.pending_orders)
        return json.dumps(data, ensure_ascii=False)

    @filter.llm_tool(name="bank_admin_confirm_order")
    async def bank_admin_confirm_order(
        self, event: AstrMessageEvent, order_id: int, amount_isk: int
    ) -> str:
        """[管理员] 确认一个消费订单并按给定额度扣款。

        参数：
        - order_id：订单号（整数）
        - amount_isk：消费额度，单位是 ISK 整数。
          例如「15 亿」要写成 1500000000，「5 亿」写成 500000000。
          不要写成「15」或「15e」。

        扣款顺序固定为：当月奖励 -> 额外津贴 -> 余额。
        余额不足时整笔拒绝、不扣任何钱，请在回复里告知用户。
        """
        qq, err = self._require_admin(event)
        if err:
            return err
        data = await self.bank_runtime.run(
            bank_handlers.confirm_order, int(order_id), int(amount_isk), qq)
        return json.dumps(data, ensure_ascii=False)

    @filter.llm_tool(name="bank_admin_reject_order")
    async def bank_admin_reject_order(
        self, event: AstrMessageEvent, order_id: int
    ) -> str:
        """[管理员] 驳回一个消费订单（不动账）。参数 order_id 为订单号。"""
        qq, err = self._require_admin(event)
        if err:
            return err
        data = await self.bank_runtime.run(
            bank_handlers.reject_order, int(order_id), qq)
        return json.dumps(data, ensure_ascii=False)

    @filter.llm_tool(name="bank_admin_accounts")
    async def bank_admin_accounts(self, event: AstrMessageEvent) -> str:
        """[管理员] 查看全部成员账户的余额、津贴、当月奖励总览。"""
        qq, err = self._require_admin(event)
        if err:
            return err
        data = await self.bank_runtime.run(bank_handlers.accounts_table)
        return json.dumps(data, ensure_ascii=False)

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
        try:
            self.bank_runtime.close()
        except Exception as exc:
            logger.warning(f"关闭成员银行数据库失败：{exc}")
