"""EVE 工具函数 - MCP 中间件已接管所有功能工具，此文件仅保留辅助函数"""

import json
from astrbot.core.agent.tool import ToolExecResult


def eve_error(message: str) -> ToolExecResult:
    return f"error: {message}"


def eve_json_result(data) -> ToolExecResult:
    return json.dumps(data, ensure_ascii=False)
