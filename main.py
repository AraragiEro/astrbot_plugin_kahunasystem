from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api import AstrBotConfig

from .src.event import Event, set_command_prefix
from .src.api_client import set_service_api_key
from .src.identity import IdentityStore, set_identity_store
from .src.picture_render_server.picture_render import set_t2i_renderer


@register("helloworld", "YourName", "一个简单的 Hello World 插件", "1.0.0")
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

    async def initialize(self):
        """可选的异步初始化方法。"""

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

    async def terminate(self):
        """可选的异步销毁方法。"""
