from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api.message_components import Image
from astrbot.core.message.components import Plain
from astrbot.core import logger
import aiohttp
import asyncio

from .picture_render_server.render_template import render_price_res_pic, render_single_cost_pic

calculate_lock = asyncio.Lock()
async def try_acquire_lock(lock, timeout=0.01):
    """尝试非阻塞地获取锁"""
    try:
        await asyncio.wait_for(lock.acquire(), timeout=timeout)
        return True
    except asyncio.TimeoutError:
        return False

class Event():
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

        # 从本地 API 获取价格与历史数据
        api_url = f"http://{Event.config['kahunasystem_host']}/api/astrbot/market/price_detail"
        payload = {"type_name": item_name}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(api_url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        return event.plain_result(f"价格接口请求失败: HTTP {resp.status}")
                    res_json = await resp.json()
        except Exception as e:
            logger.error(f"价格接口请求异常: {e}")
            return event.plain_result("价格接口请求异常，请稍后再试。")

        is_price = res_json.get("is_price", False)
        if not is_price:
            fuzz_list = res_json.get("data", []) or []
            if not fuzz_list:
                return event.plain_result(f"物品 {item_name} 未找到。")
            fuzz_reply = (f"物品 {item_name} 不存在于数据库\n"
                          f"你是否在寻找:\n")
            fuzz_reply += "\n".join(fuzz_list)
            return event.plain_result(fuzz_reply)

        data = res_json.get("data", {}) or {}
        history_data = data.get("history_data", []) or []
        chart_history_data = [[row[0], row[1]] for row in history_data]
        order_data = data.get("order_data") or {"buy_order": {}, "sell_order": {}}

        quantity_str = ''

        res_path = await render_price_res_pic(
            data,
            chart_history_data,
            order_data,
        )
        chain = [
            Image.fromFileSystem(res_path)
        ]
        if quantity > 1:
            quantity_str += f'--------鎬昏--------\n'
            min_sell = data.get("sell", 0)
            max_buy = data.get("buy", 0)
            mid_price = data.get("mid", 0)
            quantity_str += f'sell: {min_sell * quantity:,}\n'
            quantity_str += f'buy: {max_buy * quantity:,}\n'
            quantity_str += f'mid: {mid_price * quantity:,}\n'
            chain += [Plain(quantity_str)]
        return event.chain_result(chain)

    @staticmethod
    async def costdetail(event: AstrMessageEvent, product: str, username: str = None, plan_name: str = None):
        if await try_acquire_lock(calculate_lock, 1):
            try:
                type_name = product
                if not type_name:
                    return event.plain_result("未提供物品名称。")

                api_url = f"http://{Event.config['kahunasystem_host']}/api/astrbot/market/type_cost"
                payload = {
                    "type_name": type_name,
                    "username": username or Event.config['cost_username'],
                    "plan_name": plan_name or Event.config['cost_plan']
                }
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.post(api_url, json=payload, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                            if resp.status != 200:
                                return event.plain_result(f"成本接口请求失败: HTTP {resp.status}")
                            res_json = await resp.json()
                except Exception as e:
                    logger.error(f"成本接口请求异常: {e}")
                    return event.plain_result("成本接口请求异常，请稍后再试。")

                if not res_json.get("is_cost", False):
                    return event.plain_result(f"物品 {type_name} 未找到成本数据。")

                detail_dict = res_json.get("data", {}) or {}
                pic_path = await render_single_cost_pic(detail_dict)

                chain = [
                    Image.fromFileSystem(pic_path)
                ]
                return event.chain_result(chain)
            finally:
                calculate_lock.release()
        else:
            return event.plain_result("已有成本计算进行中，请稍候再试。")
