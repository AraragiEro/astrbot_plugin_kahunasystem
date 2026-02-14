from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api import AstrBotConfig
import json
from pathlib import Path

from .src.event import Event
from .src.tools.eve_tools import ApiInfoTool, ApiListTool, eve_error, eve_json_result
from .src.api_client import (
    api_run,
    api_cj_get_paps_status,
    api_cj_get_tmp_result,
    api_cj_init,
    api_cj_next_round,
    api_cj_run,
    api_cj_save_state,
    api_cj_set_active,
    api_cj_set_user_paps_used,
)


@register("helloworld", "YourName", "一个简单的 Hello World 插件", "1.0.0")
class MyPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        Event.config = self.config
        self.context.add_llm_tools(ApiListTool())
        self.context.add_llm_tools(ApiInfoTool())

    async def initialize(self):
        """可选的异步初始化方法。"""

    @staticmethod
    def _extract_role_name(name: str) -> str:
        nickname = (name or "").strip()
        if "/" in nickname:
            return nickname.rsplit("/", 1)[-1].strip()
        return nickname

    @staticmethod
    def _cj_usage() -> str:
        return (
            "抽奖指令用法：\n"
            ".cj init 初始化抽奖状态（管理员）\n"
            ".cj on 开启抽奖录入（管理员）\n"
            ".cj off 关闭抽奖录入（管理员）\n"
            ".cj run 执行抽奖结算（管理员）\n"
            ".cj next 切换到下一轮（管理员）\n"
            ".cj save 保存抽奖状态（管理员）\n"
            ".cj paps [int] 录入本轮消耗 PAP（成员）"
        )

    @staticmethod
    def _format_api_error(res_json: dict, default: str) -> str:
        if not isinstance(res_json, dict):
            return default
        message = res_json.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
        return default

    @staticmethod
    def _tmp_cursor_file() -> Path:
        return Path("tmp") / "cj_tmp_cursor.json"

    @staticmethod
    def _chunk_ten_draws(logs: list) -> list:
        if not isinstance(logs, list):
            return []
        return [logs[i:i + 10] for i in range(0, len(logs), 10)]

    @staticmethod
    def _build_rewards_summary(data: dict) -> list:
        rewards_by_user = data.get("rewards_by_user") or []
        if not isinstance(rewards_by_user, list):
            return []
        summary = []
        for user_item in rewards_by_user:
            if not isinstance(user_item, dict):
                continue
            rewards = user_item.get("rewards") or []
            reward_lines = []
            if isinstance(rewards, list):
                for reward in rewards:
                    if not isinstance(reward, dict):
                        continue
                    reward_lines.append({
                        "reward_name": reward.get("reward_name"),
                        "rarity": reward.get("rarity"),
                        "count": reward.get("count", 0),
                    })
            summary.append({
                "name": user_item.get("name"),
                "user_id": user_item.get("user_id"),
                "total_reward_count": user_item.get("total_reward_count", 0),
                "rewards": reward_lines,
            })
        return summary

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

    @filter.command("ssid")
    async def ssid(self, event: AstrMessageEvent):
        yield event.plain_result(f"你的 session id 是: {event.get_session_id()}")

    @filter.command_group("cj")
    def cj(self):
        """抽奖指令组。"""
        pass

    @filter.permission_type(filter.PermissionType.ADMIN)
    @cj.command("init")
    async def cj_init(self, event: AstrMessageEvent):
        try:
            res_json = await api_cj_init(self.config["kahunasystem_host"])
        except Exception as e:
            logger.error(f"cj init 调用异常: {e}")
            yield event.plain_result(f"初始化异常：{e}\n{self._cj_usage()}")
            return
        if res_json.get("status") != 200:
            yield event.plain_result(f"初始化失败：{self._format_api_error(res_json, 'init 调用失败')}")
            return
        yield event.plain_result("抽奖状态初始化成功。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @cj.command("on")
    async def cj_on(self, event: AstrMessageEvent):
        try:
            res_json = await api_cj_set_active(self.config["kahunasystem_host"], True)
        except Exception as e:
            logger.error(f"cj on 调用异常: {e}")
            yield event.plain_result(f"开启录入异常：{e}\n{self._cj_usage()}")
            return
        if res_json.get("status") != 200:
            yield event.plain_result(f"开启录入失败：{self._format_api_error(res_json, 'set_active 调用失败')}")
            return
        yield event.plain_result("已开启抽奖信息录入。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @cj.command("off")
    async def cj_off(self, event: AstrMessageEvent):
        try:
            res_json = await api_cj_set_active(self.config["kahunasystem_host"], False)
        except Exception as e:
            logger.error(f"cj off 调用异常: {e}")
            yield event.plain_result(f"关闭录入异常：{e}\n{self._cj_usage()}")
            return
        if res_json.get("status") != 200:
            yield event.plain_result(f"关闭录入失败：{self._format_api_error(res_json, 'set_active 调用失败')}")
            return
        yield event.plain_result("已关闭抽奖信息录入。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @cj.command("run")
    async def cj_run(self, event: AstrMessageEvent):
        try:
            res_json = await api_cj_run(self.config["kahunasystem_host"])
        except Exception as e:
            logger.error(f"cj run 调用异常: {e}")
            yield event.plain_result(f"执行抽奖异常：{e}\n{self._cj_usage()}")
            return
        if res_json.get("status") != 200:
            yield event.plain_result(f"执行抽奖失败：{self._format_api_error(res_json, 'run 调用失败')}")
            return
        yield event.plain_result("抽奖已执行完成，可通知ai 查看结果。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @cj.command("next")
    async def cj_next(self, event: AstrMessageEvent):
        try:
            res_json = await api_cj_next_round(self.config["kahunasystem_host"])
        except Exception as e:
            logger.error(f"cj next 调用异常: {e}")
            yield event.plain_result(f"切换轮次异常：{e}\n{self._cj_usage()}")
            return
        if res_json.get("status") != 200:
            yield event.plain_result(f"切换下一轮失败：{self._format_api_error(res_json, 'next_round 调用失败')}")
            return
        yield event.plain_result("已切换到下一轮抽奖。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @cj.command("save")
    async def cj_save(self, event: AstrMessageEvent):
        try:
            res_json = await api_cj_save_state(self.config["kahunasystem_host"])
        except Exception as e:
            logger.error(f"cj save 调用异常: {e}")
            yield event.plain_result(f"保存状态异常：{e}\n{self._cj_usage()}")
            return
        if res_json.get("status") != 200:
            yield event.plain_result(f"保存状态失败：{self._format_api_error(res_json, 'save_state 调用失败')}")
            return
        yield event.plain_result(res_json.get("message") or "抽奖状态保存成功。")

    @cj.command("help")
    async def cj_help(self, event: AstrMessageEvent):
        yield event.plain_result(self._cj_usage())

    @cj.command("paps")
    async def cj_paps(self, event: AstrMessageEvent, paps_value: str = None):
        if paps_value is None:
            parts = (event.get_message_str() or "").strip().split()
            paps_value = parts[2] if len(parts) >= 3 else None
        if not paps_value:
            yield event.plain_result("参数错误：缺少 PAP 数量。\n用法：.cj paps [int]")
            return
        try:
            paps = int(paps_value)
        except Exception:
            yield event.plain_result("参数错误：PAP 数量必须是整数。\n用法：.cj paps [int]")
            return
        if paps < 0:
            yield event.plain_result("参数错误：PAP 数量不能为负数。\n用法：.cj paps [int]")
            return

        role_name = self._extract_role_name(event.get_sender_name())
        if not role_name:
            yield event.plain_result("无法识别角色名，请检查你的群昵称是否可读。")
            return

        try:
            res_json = await api_cj_set_user_paps_used(self.config["kahunasystem_host"], role_name, paps)
        except Exception as e:
            logger.error(f"cj paps 调用异常: {e}")
            yield event.plain_result(f"录入 PAP 异常：{e}\n请确认后端服务可用。")
            return
        if res_json.get("status") != 200:
            yield event.plain_result(
                f"录入 PAP 失败：{self._format_api_error(res_json, 'set_user_paps_used 调用失败')}\n"
                "请确认抽奖录入已开启（.cj on）且角色名配置正确。"
            )
            return
        data = res_json.get("data") or {}
        remain = data.get("remain_paps", "未知")
        used = data.get("this_round_used_paps", "未知")
        yield event.plain_result(f"录入成功：角色 {role_name} 本轮已录入 {used} PAP，剩余 PAP {remain}。")

    async def terminate(self):
        """可选的异步销毁方法。"""

    @filter.llm_tool(name="kahunasystem_apirun")
    async def kahunasystem_apirun(self, event: AstrMessageEvent, api_id: str, access_token: str, eve_args: dict) -> MessageEventResult:
        """运行 kahunasystem 的 API。"""
        try:
            res_json = await api_run(self.config["kahunasystem_host"], api_id, eve_args, event.get_sender_id(), access_token)
        except Exception as e:
            return eve_error(f"api run request failed: {e}")
        if res_json.get("status") == 400:
            message = res_json.get("message", "api run failed")
            return eve_error(message)
        return eve_json_result(res_json)

    @filter.llm_tool(name="get_tmp_result")
    async def get_tmp_result(self, event: AstrMessageEvent, fetch_remote: bool = False) -> MessageEventResult:
        """工具说明（供 AI 调用）：
        获取抽奖临时结果。
        - 用户说“获取抽奖结果”时，fetch_remote=true：从远端获取最新结果，并重置十连展示进度,此时不要输出结果，等待用户十连抽卡；
        - 用户说“十连抽卡”时，fetch_remote=false：按顺序返回下一组 10 连结果，根据结果使用雌小鬼语气讽刺用户或恭喜用户；
        - 当没有更多 10 连结果时，返回用户奖励汇总并提示抽奖已完成。
        - 当用户说获取SSR结果时，fetch_remote=false：返回SSR结果，并根据结果使用雌小鬼语气讽刺用户或恭喜用户；
        - pity_before: SSR保底计数器前值
        - pity_after: SSR保底计数器后值
        SSR权重是最终获取SSR奖品的概率，越高越好！

        Args:
            fetch_remote(boolean): 是否从远端获取最新结果
        """
        cursor_file = self._tmp_cursor_file()

        try:
            local_res = await api_cj_get_tmp_result(self.config["kahunasystem_host"])
        except Exception as e:
            return eve_error(f"get_tmp_result request failed: {e}")
        if local_res.get("status") != 200:
            return eve_error(self._format_api_error(local_res, "get_tmp_result failed"))

        data = local_res.get("data") or {}
        logs = data.get("draw_settlement_logs") or []
        batches = self._chunk_ten_draws(logs)
        total_batches = len(batches)

        if fetch_remote:
            try:
                cursor_file.parent.mkdir(parents=True, exist_ok=True)
                cursor_file.write_text(json.dumps({"next_batch_index": 0}, ensure_ascii=False), encoding="utf-8")
            except Exception as e:
                return eve_error(f"reset local cursor failed: {e}")
            return eve_json_result({
                "message": "已从远端获取最新结果并重置展示进度, 总计{total_batches}组",
                "ten_draw_count": total_batches,
            })

        next_batch_index = 0
        if cursor_file.exists():
            try:
                cursor = json.loads(cursor_file.read_text(encoding="utf-8"))
                next_batch_index = int(cursor.get("next_batch_index", 0))
            except Exception:
                next_batch_index = 0

        if next_batch_index < total_batches:
            batch = batches[next_batch_index]
            remaining = total_batches - next_batch_index - 1
            try:
                cursor_file.parent.mkdir(parents=True, exist_ok=True)
                cursor_file.write_text(
                    json.dumps({"next_batch_index": next_batch_index + 1}, ensure_ascii=False),
                    encoding="utf-8",
                )
            except Exception as e:
                return eve_error(f"update local cursor failed: {e}")

            # 压缩10连返回字段，降低上下文消耗
            compact_batch = []
            for row in batch:
                if not isinstance(row, dict):
                    continue
                compact_batch.append({
                    "no": row.get("draw_no"),
                    "name": row.get("name"),
                    "type": row.get("result_type"),
                    "reward": row.get("reward_name"),
                    "rarity": row.get("rarity"),
                    "pity_before": row.get("pity_before"),
                    "pity_after": row.get("pity_after"),
                })

            return eve_json_result({
                "message": f"返回一组10连结果, 剩余{remaining}组",
                "current_ten_draw_index": next_batch_index + 1,
                "remaining_ten_draw_count": remaining,
                "draw_results": compact_batch,
            })

        rewards_summary = self._build_rewards_summary(data)
        return eve_json_result({
            "message": "抽奖已完成",
            "remaining_ten_draw_count": 0,
            "rewards_by_user": rewards_summary,
        })

    @filter.llm_tool(name="get_paps_status")
    async def get_paps_status(self, event: AstrMessageEvent, name: str = None) -> MessageEventResult:
        """工具说明（供 AI 调用）：
        根据角色名查询用户 PAP 状态与保底计数器状态。
        - 当未传 name 时，从发送者昵称提取角色名；
        - 昵称提取规则参考“医保查询”：若含 `/`，取最后一个 `/` 后半段。

        Args:
            name(string): 角色名, 
        """
        role_name = self._extract_role_name(name or event.get_sender_name())
        if not role_name:
            return eve_error("name is required")
        try:
            res_json = await api_cj_get_paps_status(self.config["kahunasystem_host"], role_name)
        except Exception as e:
            return eve_error(f"get_paps_status request failed: {e}")
        if res_json.get("status") != 200:
            return eve_error(self._format_api_error(res_json, "get_paps_status failed"))
        return eve_json_result(res_json)
