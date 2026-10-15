from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api import AstrBotConfig

from .src.event import Event
from .src.api_client import set_service_api_key
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

    @filter.command("ssid")
    async def ssid(self, event: AstrMessageEvent):
        yield event.plain_result(f"你的 session id 是: {event.get_session_id()}")

    async def terminate(self):
        """可选的异步销毁方法。"""
