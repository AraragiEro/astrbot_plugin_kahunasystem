import json

from pydantic import Field
from pydantic.dataclasses import dataclass

from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool, ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext
from ..api_client import api_info, api_list, api_run, get_json
from ..event import Event
from ..utils import get_id

def eve_error(message: str) -> ToolExecResult:
    return f"error: {message}"


def eve_json_result(data) -> ToolExecResult:
    return json.dumps(data, ensure_ascii=False)

@dataclass
class ApiListTool(FunctionTool[AstrAgentContext]):
    name: str = "kahunasystem_apilist"
    description: str = "列出kahunasystem可用的 API 条目。重要：仅包含api_id和description信息。参数与返回的详细信息请使用kahunasystem_apiinfo确认。不可直接使用kahunasystem_apirun调用"
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {},
            "required": [],
        }
    )

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        try:
            res_json = await api_list(Event.config["kahunasystem_host"])
        except Exception as e:
            return eve_error(f"api list request failed: {e}")
        if res_json.get("status") != 200:
            message = res_json.get("message", "api list failed")
            return eve_error(message)
        data = res_json.get("data", []) or []
        return eve_json_result(data)


@dataclass
class ApiInfoTool(FunctionTool[AstrAgentContext]):
    name: str = "kahunasystem_apiinfo"
    description: str = "根据 API id 获取 API 详情, 并获取一个访问kahunasystem_apirun必须的access_token, access_token只能使用一次。重要：在使用kahunasystem_apirun前必须调用获取参数和返回案例，非常重要。"
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "api_id": {
                    "type": "string",
                    "description": "Target api id",
                }
            },
            "required": ["api_id"],
        }
    )

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        api_id = kwargs.get("api_id")
        if not api_id:
            return eve_error("api_id is required")

        try:
            res_json = await api_info(Event.config["kahunasystem_host"], api_id)
        except Exception as e:
            return eve_error(f"api info request failed: {e}")
        if res_json.get("status") != 200:
            message = res_json.get("message", "api info failed")
            return eve_error(message)
        data = res_json.get("data", []) or []
        return eve_json_result(data)

@dataclass
class ZkbUrlData(FunctionTool[AstrAgentContext]):
    name: str = "analyse_zkb_url_data_summary"
    description: str = """
    分析zkb url返回的数据，提取有用信息，生成一个简洁的报告。
    return:
    {
    "totalValueTotal": kb的总价值，菜菜的,
    "droppedValueTotal": kb中被拾取的价值总和，资敌了,
    "destroyedValueTotal": kb中被摧毁的价值总和，从宇宙中蒸发！,
    }
    """
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "zkb url",
                }
            },
            "required": ["url"],
        }
    )

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        url = kwargs.get("url")
        if not url:
            return eve_error("url is required")
        # 这里直接返回url，后续分析工具会使用这个url获取数据并进行分析，避免一次性获取大量数据导致token消耗过大。
        
        try:
            res_json = await get_json(url, "", timeout=10)

            res = {
                "totalValueTotal": 0,
                "droppedValueTotal": 0,
                "destroyedValueTotal": 0,
            }
            for kb in res_json:
                totalValue = kb["zkb"].get("totalValue", 0)
                droppedValue = kb["zkb"].get("droppedValue", 0)
                destroyedValue = kb["zkb"].get("destroyedValue", 0)
                res["totalValueTotal"] += totalValue
                res["droppedValueTotal"] += droppedValue
                res["destroyedValueTotal"] += destroyedValue
            return eve_json_result(res)

        except Exception as e:
            raise e


@dataclass
class Name2ID(FunctionTool[AstrAgentContext]):
    name: str = "get_id_by_name"
    description: str = """根据EVE实体的名称精确获得对应id。
    类似SETCR这种5个字母或数字或特殊字符的字符串是公司或联盟简写
    类似Alero AraragiEro这种1个或两个单词的可能是角色名，大于两个单词的一般是公司名，这种需要查询两次
    类似jita，Q-CAB2这种单个单词或有-的是星系名"""
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "EVE实体名称",
                },
                "type": {
                    "type": "string",
                    "description": "EVE实体类型，当前支持：[characters, corporations, systems]",
                }
            },
            "required": ["name", "type"],
        }
    )

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        name = kwargs.get("name")
        entity_type = kwargs.get("type")
        if not name or not entity_type:
            return eve_error("name and type are required")
        
        try:
            id = await get_id(entity_type, name)
            return eve_json_result({"id": id})

        except Exception as e:
            raise e