from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api.message_components import Image
from astrbot.core.message.components import Plain
from astrbot.core import logger
import asyncio

from .picture_render_server.render_template import (
    render_price_res_pic,
    render_single_cost_pic,
)
from .api_client import api_price_detail, api_frt_price_detail, api_type_cost, api_qq_bind, api_info

calculate_lock = asyncio.Lock()


async def try_acquire_lock(lock, timeout=0.01):
    """尝试非阻塞地获取锁"""
    try:
        await asyncio.wait_for(lock.acquire(), timeout=timeout)
        return True
    except asyncio.TimeoutError:
        return False


def parse_allow_group_ids(raw_value):
    if not raw_value:
        return set()
    if isinstance(raw_value, (list, tuple, set)):
        return {str(item).strip() for item in raw_value if str(item).strip()}
    if isinstance(raw_value, str):
        return {
            item.strip()
            for item in raw_value.replace("，", ",").split(",")
            if item.strip()
        }
    return set()


def _to_float(value, default: float = 0.0) -> float:
    """安全转换浮点数，避免接口返回字符串或 None 时崩溃。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def price_text_fallback(data: dict, quantity: int, source: str) -> str:
    """图片渲染不可用时的纯文本价格摘要。"""
    item_name = (
        data.get("name_zh")
        or data.get("matched_name")
        or data.get("name")
        or str(data.get("type_id", ""))
    )
    buy = _to_float(data.get("buy"))
    mid = _to_float(data.get("mid"))
    sell = _to_float(data.get("sell"))

    lines = [
        f"【{source} 价格】{item_name}（图片渲染不可用，以下为文本结果）",
        f"buy: {buy:,.2f}",
        f"mid: {mid:,.2f}",
        f"sell: {sell:,.2f}",
    ]
    if quantity > 1:
        lines.append(f"--------总计 x{quantity}--------")
        lines.append(f"buy: {buy * quantity:,.2f}")
        lines.append(f"mid: {mid * quantity:,.2f}")
        lines.append(f"sell: {sell * quantity:,.2f}")
    return "\n".join(lines)


def cost_text_fallback(detail: dict) -> str:
    """图片渲染不可用时的纯文本成本摘要。"""
    item_name = detail.get("item_name") or str(detail.get("type_id", ""))
    item_name_cn = detail.get("item_name_cn") or item_name
    total_cost = _to_float(detail.get("total_cost"))
    market_detail = detail.get("market_detail", []) or []
    jita_buy = _to_float(market_detail[0]) if len(market_detail) > 0 else 0.0
    jita_mid = _to_float(market_detail[1]) if len(market_detail) > 1 else 0.0
    jita_sell = _to_float(market_detail[2]) if len(market_detail) > 2 else 0.0
    profit = jita_sell - total_cost
    profit_rate = (profit / total_cost * 100) if total_cost else 0.0

    lines = [
        f"【成本】{item_name_cn}（图片渲染不可用，以下为文本结果）",
        f"总成本: {total_cost:,.2f}",
        f"Jita buy: {jita_buy:,.2f}",
        f"Jita mid: {jita_mid:,.2f}",
        f"Jita sell: {jita_sell:,.2f}",
        f"利润: {profit:,.2f}（{profit_rate:.2f}%）",
    ]
    user_name = detail.get("user_name")
    plan_name = detail.get("plan_name")
    if user_name or plan_name:
        lines.append(f"方案: {user_name or '-'} / {plan_name or '-'}")
    return "\n".join(lines)


class Event:
    config = None

    @staticmethod
    async def oprice(event: AstrMessageEvent, require_str: str):
        message_str = event.get_message_str()
        if message_str.split(" ")[-1].isdigit():
            quantity = int(message_str.split(" ")[-1])
            item_name = " ".join(message_str.split(" ")[1:-1])
        else:
            item_name = require_str
            quantity = 1

        # 先获取 access_token
        try:
            info_res = await api_info(
                Event.config["kahunasystem_host"],
                "market_price_detail",
            )
        except Exception as e:
            logger.error(f"获取token异常: {e}")
            return event.plain_result("获取访问令牌失败，请稍后再试。")

        if info_res.get("status") != 200:
            message = info_res.get("message", "获取访问令牌失败")
            return event.plain_result(f"获取访问令牌失败: {message}")

        access_token = info_res.get("data", {}).get("access_token")
        if not access_token:
            return event.plain_result("获取访问令牌失败，请稍后再试。")

        # 从本地 API 获取价格与历史数据
        try:
            res_json = await api_price_detail(
                Event.config["kahunasystem_host"],
                item_name,
                access_token,
            )
        except Exception as e:
            logger.error(f"价格接口请求异常: {e}")
            if isinstance(e, ValueError):
                return event.plain_result(f"价格接口请求失败: {e}")
            return event.plain_result("价格接口请求异常，请稍后再试。")

        is_price = res_json.get("is_price", False)
        if not is_price:
            fuzz_list = res_json.get("data", []) or []
            if not fuzz_list:
                return event.plain_result(f"物品 {item_name} 未找到。")
            fuzz_reply = f"物品 {item_name} 不存在于数据库\n你是否在寻找:\n"
            fuzz_reply += "\n".join(fuzz_list)
            return event.plain_result(fuzz_reply)

        data = res_json.get("data", {}) or {}
        history_data = data.get("history_data", []) or []
        chart_history_data = [[row[0], row[1]] for row in history_data]
        # 统一使用后端字段名：orderdata
        order_data = data.get("orderdata") or {"buy_order": {}, "sell_order": {}}

        quantity_str = ""

        try:
            res_path = await render_price_res_pic(
                data,
                chart_history_data,
                order_data,
            )
        except Exception as e:
            logger.error(f"渲染价格图片失败: {e}")
            return event.plain_result(price_text_fallback(data, quantity, "Jita"))

        chain = [Image.fromFileSystem(res_path)]
        if quantity > 1:
            quantity_str += f"--------总计--------\n"
            min_sell = data.get("sell", 0)
            max_buy = data.get("buy", 0)
            mid_price = data.get("mid", 0)
            quantity_str += f"sell: {min_sell * quantity:,}\n"
            quantity_str += f"buy: {max_buy * quantity:,}\n"
            quantity_str += f"mid: {mid_price * quantity:,}\n"
            chain += [Plain(quantity_str)]
        return event.chain_result(chain)

    @staticmethod
    async def frt_price(event: AstrMessageEvent, require_str: str):
        """查询 FRT 市场价格。"""
        message_str = event.get_message_str()
        if message_str.split(" ")[-1].isdigit():
            quantity = int(message_str.split(" ")[-1])
            item_name = " ".join(message_str.split(" ")[1:-1])
        else:
            item_name = require_str
            quantity = 1

        try:
            info_res = await api_info(
                Event.config["kahunasystem_host"],
                "market_price_detail",
            )
        except Exception as e:
            logger.error(f"获取token异常: {e}")
            return event.plain_result("获取访问令牌失败，请稍后再试。")

        if info_res.get("status") != 200:
            message = info_res.get("message", "获取访问令牌失败")
            return event.plain_result(f"获取访问令牌失败: {message}")

        access_token = info_res.get("data", {}).get("access_token")
        if not access_token:
            return event.plain_result("获取访问令牌失败，请稍后再试。")

        try:
            res_json = await api_frt_price_detail(
                Event.config["kahunasystem_host"],
                item_name,
                access_token,
            )
        except Exception as e:
            logger.error(f"FRT价格接口请求异常: {e}")
            if isinstance(e, ValueError):
                return event.plain_result(f"FRT价格接口请求失败: {e}")
            return event.plain_result("FRT价格接口请求异常，请稍后再试。")

        is_price = res_json.get("is_price", False)
        if not is_price:
            fuzz_list = res_json.get("data", []) or []
            if not fuzz_list:
                return event.plain_result(f"物品 {item_name} 未找到。")
            fuzz_reply = f"物品 {item_name} 不存在于数据库\n你是否在寻找:\n"
            fuzz_reply += "\n".join(fuzz_list)
            return event.plain_result(fuzz_reply)

        data = res_json.get("data", {}) or {}
        history_data = data.get("history_data", []) or []
        chart_history_data = [[row[0], row[1]] for row in history_data]
        order_data = data.get("orderdata") or {"buy_order": {}, "sell_order": {}}

        quantity_str = ""

        try:
            res_path = await render_price_res_pic(
                data,
                chart_history_data,
                order_data,
            )
        except Exception as e:
            logger.error(f"渲染价格图片失败: {e}")
            return event.plain_result(price_text_fallback(data, quantity, "FRT"))

        chain = [Image.fromFileSystem(res_path)]
        if quantity > 1:
            quantity_str += f"--------总计--------\n"
            min_sell = data.get("sell", 0)
            max_buy = data.get("buy", 0)
            mid_price = data.get("mid", 0)
            quantity_str += f"sell: {min_sell * quantity:,}\n"
            quantity_str += f"buy: {max_buy * quantity:,}\n"
            quantity_str += f"mid: {mid_price * quantity:,}\n"
            chain += [Plain(quantity_str)]
        return event.chain_result(chain)

    @staticmethod
    async def costdetail(
        event: AstrMessageEvent,
        product: str,
        username: str = None,
        plan_name: str = None,
    ):
        if await try_acquire_lock(calculate_lock, 1):
            try:
                type_name = product
                if not type_name:
                    return event.plain_result("未提供物品名称。")

                # 先获取 access_token
                try:
                    info_res = await api_info(
                        Event.config["kahunasystem_host"],
                        "market_type_cost",
                    )
                except Exception as e:
                    logger.error(f"获取token异常: {e}")
                    return event.plain_result("获取访问令牌失败，请稍后再试。")

                if info_res.get("status") != 200:
                    message = info_res.get("message", "获取访问令牌失败")
                    return event.plain_result(f"获取访问令牌失败: {message}")

                access_token = info_res.get("data", {}).get("access_token")
                if not access_token:
                    return event.plain_result("获取访问令牌失败，请稍后再试。")

                user_name = username or Event.config["cost_username"]
                plan = plan_name or Event.config["cost_plan"]
                try:
                    res_json = await api_type_cost(
                        Event.config["kahunasystem_host"],
                        type_name,
                        user_name,
                        plan,
                        access_token,
                    )
                except Exception as e:
                    logger.error(f"成本接口请求异常: {e}")
                    if isinstance(e, ValueError):
                        return event.plain_result(f"成本接口请求失败: {e}")
                    return event.plain_result("成本接口请求异常，请稍后再试。")

                if not res_json.get("is_cost", False):
                    return event.plain_result(f"物品 {type_name} 未找到成本数据。")

                detail_dict = res_json.get("data", {}) or {}
                try:
                    pic_path = await render_single_cost_pic(detail_dict)
                except Exception as e:
                    logger.error(f"渲染成本图片失败: {e}")
                    return event.plain_result(cost_text_fallback(detail_dict))

                chain = [Image.fromFileSystem(pic_path)]
                return event.chain_result(chain)
            finally:
                calculate_lock.release()
        else:
            return event.plain_result("已有成本计算进行中，请稍候再试。")

    @staticmethod
    async def bind_kahunasystem(event: AstrMessageEvent, uuid: str):
        if not uuid:
            return event.plain_result("格式错误，缺少 uuid。")

        # group_id = event.get_group_id()
        # allow_groups = parse_allow_group_ids(Event.config.get("bind_allow_group_ids"))
        # if group_id is not None and str(group_id) not in allow_groups:
        #     return event.plain_result("该指令仅限私聊或指定群使用。")

        qq = event.get_sender_id()
        try:
            res_json = await api_qq_bind(
                Event.config["kahunasystem_host"],
                qq,
                uuid,
            )
        except Exception as e:
            logger.error(f"绑定回调请求异常: {e}")
            return event.plain_result("绑定失败，请稍后再试。")

        status = res_json.get("status")
        try:
            status = int(status)
        except Exception:
            status = None
        if status and status >= 400:
            return event.plain_result(res_json.get("message") or "绑定失败。")

        return event.plain_result("绑定成功")
