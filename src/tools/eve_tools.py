import json

from pydantic import Field
from pydantic.dataclasses import dataclass

from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool, ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext
from ..api_client import api_info, api_list, api_run
from ..event import Event


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
    description: str = "根据 API id 获取 API 详情, 在使用kahunasystem_apirun前必须调用获取参数和返回案例，非常重要。"
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "api_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "API id list",
                }
            },
            "required": ["api_ids"],
        }
    )

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        api_ids = kwargs.get("api_ids")
        if not isinstance(api_ids, list) or not api_ids:
            return eve_error("api_ids must be a non-empty list")

        try:
            res_json = await api_info(Event.config["kahunasystem_host"], api_ids)
        except Exception as e:
            return eve_error(f"api info request failed: {e}")
        if res_json.get("status") != 200:
            message = res_json.get("message", "api info failed")
            return eve_error(message)
        data = res_json.get("data", []) or []
        return eve_json_result(data)


# @dataclass
# class ApiRunTool(FunctionTool[AstrAgentContext]):
#     name: str = "kahunasystem_apirun"
#     description: str = "根据 API id 运行 API。"
#     parameters: dict = Field(
#         default_factory=lambda: {
#             "type": "object",
#             "properties": {
#                 "api_id": {
#                     "type": "string",
#                     "description": "Target api id",
#                 },
#                 "args": {
#                     "type": "object",
#                     "description": "API args object",
#                 },
#             },
#             "required": ["api_id"],
#         }
#     )

#     async def call(
#         self, context: ContextWrapper[AstrAgentContext], **kwargs
#     ) -> ToolExecResult:
#         api_id = kwargs.get("api_id")
#         if not api_id:
#             return eve_error("api_id is required")
#         args = kwargs.get("args") or {}
#         if not isinstance(args, dict):
#             return eve_error("args must be an object")

#         try:
#             res_json = await api_run(Event.config["kahunasystem_host"], api_id, args)
#         except Exception as e:
#             return eve_error(f"api run request failed: {e}")
#         if res_json.get("status") == 400:
#             message = res_json.get("message", "api run failed")
#             return eve_error(message)
#         return eve_json_result(res_json)
