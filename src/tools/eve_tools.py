from pydantic import Field
from pydantic.dataclasses import dataclass

from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool, ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext

from ..api_client import api_price_detail, api_type_cost

from ..event import Event
@dataclass
class EvePriceData(FunctionTool[AstrAgentContext]):
    name: str = "eve_price_data"  # 工具名称
    description: str = "一个工具可以获取eve online中物品的jita价格数据，包括当前价格，当前买卖订单前五，历史365天的数据."  # 工具描述
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "type_name": {
                    "type": "string",
                    "description": "物品名称，如：wyvern",
                },
            },
            "required": ["type_name"],
        }
    )

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        if "type_name" not in kwargs:
            return ToolExecResult(success=False, error="type_name is required")
        type_name = kwargs["type_name"]
        if not type_name:
            return ToolExecResult(success=False, error="type_name is required")

        try:
            res_json = await api_price_detail(
                Event.config["kahunasystem_host"],
                type_name,
            )
        except Exception as e:
            return ToolExecResult(success=False, error=f"价格接口请求失败: {e}")
        if not res_json.get("is_price", False):
            return ToolExecResult(success=False, error=f"物品 {type_name} 未找到。")
        data = res_json.get("data", {}) or {}
        
        return data

@dataclass
class EveCostData(FunctionTool[AstrAgentContext]):
    name: str = "eve_cost_data"  # 工具名称
    description: str = "一个工具可以获取eve online中物品的成本数据，如果需要计算利润，则使用该工具获取成本，在使用eve_price_data获取价格."  # 工具描述
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "type_name": {
                    "type": "string",
                    "description": "物品名称，如：wyvern",
                }
            },
            "required": ["type_name"],
        }
    )

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        if "type_name" not in kwargs:
            return ToolExecResult(success=False, error="type_name is required")
        type_name = kwargs["type_name"]
        if not type_name:
            return ToolExecResult(success=False, error="type_name is required")
        username =  Event.config['cost_username']
        plan_name = Event.config['cost_plan']
        try:
            res_json = await api_type_cost(
                Event.config["kahunasystem_host"],
                type_name,
                username,
                plan_name,
            )
        except Exception as e:
            return ToolExecResult(success=False, error=f"成本接口请求失败: {e}")
        if not res_json.get("is_cost", False):
            return ToolExecResult(success=False, error=f"物品 {type_name} 未找到成本数据。")
        data = res_json.get("data", {}) or {}
        return data