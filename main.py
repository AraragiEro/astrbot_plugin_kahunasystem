from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger

from .src.event import Event
from astrbot.api import AstrBotConfig

from .src.tools.eve_tools import EvePriceData

@register("helloworld", "YourName", "一个简单的 Hello World 插件", "1.0.0")
class MyPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        print(self.config)
        Event.config = self.config

        self.context.add_llm_tools(EvePriceData())

    async def initialize(self):
        """可选择实现异步的插件初始化方法，当实例化该插件类之后会自动调用该方法。"""

    # 注册指令的装饰器。指令名为 helloworld。注册成功后，发送 `/helloworld` 就会触发这个指令，并回复 `你好, {user_name}!`
    @filter.command("helloworld")
    async def helloworld(self, event: AstrMessageEvent):
        """这是一个 hello world 指令""" # 这是 handler 的描述，将会被解析方便用户了解插件内容。建议填写。
        user_name = event.get_sender_name()
        message_str = event.message_str # 用户发的纯文本消息字符串
        message_chain = event.get_messages() # 用户所发的消息的消息链 # from astrbot.api.message_components import *
        logger.info(message_chain)
        yield event.plain_result(f"Hello, {user_name}, 你发了 {message_str}!") # 发送一条纯文本消息


    @filter.command("ojita")
    async def cost(self, event: AstrMessageEvent, require_str: str):
        ''' 这是一个查询jita市场价格的插件 '''
        yield await Event.oprice(event, require_str)

    @filter.command("成本", alias={"cost"})
    async def costdetail(self, event: AstrMessageEvent, product: str, username: str = None, plan_name: str = None):
        ''' 这是一个查询成本详情的插件 '''
        yield await Event.costdetail(event, product, username, plan_name)

    async def terminate(self):
        """可选择实现异步的插件销毁方法，当插件被卸载/停用时会调用。"""
