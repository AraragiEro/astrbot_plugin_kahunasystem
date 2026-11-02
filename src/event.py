import asyncio

from astrbot.api.event import AstrMessageEvent, MessageChain
from astrbot.api.message_components import At, Image, Reply
from astrbot.core.message.components import Plain
from astrbot.core import logger

from .api_client import ApiError, bind_qq, clip_text
from .mcp_client import DEFAULT_MCP_URL, KahunaMcpClient, McpError
from .picture_render_server.render_template import (
    render_price_res_pic,
    render_single_cost_pic,
)

calculate_lock = asyncio.Lock()

# QQ 官方机器人平台标识（这类平台的发送者 id 是 openid，不是 QQ 号）
QQ_OFFICIAL_PLATFORMS = ("qq_official", "qq_official_webhook")


async def try_acquire_lock(lock, timeout=0.01):
    """尝试非阻塞地获取锁"""
    try:
        await asyncio.wait_for(lock.acquire(), timeout=timeout)
        return True
    except asyncio.TimeoutError:
        return False


def _to_float(value, default: float = 0.0) -> float:
    """安全转换浮点数，避免接口返回字符串或 None 时崩溃。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def api_error_for_user(scene: str) -> str:
    """接口异常时回给 QQ 群的固定文案。

    ⚠️ 群里**不放任何错误细节**：状态码、后端 message、响应体一律不外发。
    后端/网关（nginx、Cloudflare）出错时返回的是 HTML 页面，动辄几十 KB，
    一旦拼进 plain_result 就会把整页 HTML/JS 灌进群（2026-10-17 线上踩过：
    `成本接口请求失败: HTTP 405, message: <!doctype html>…`）。
    排查信息只写日志，需要定位问题时看 AstrBot 日志。
    """
    return f"{scene}暂时不可用，请稍后再试。"


def price_summary(data: dict, quantity: int, source: str, note: str = "") -> str:
    """价格文本摘要。

    两处用途：出图前单独发的 @ 摘要消息；图片渲染不可用时的降级文本
    （此时传 note 说明）。
    """
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
        f"【{source} 价格】{item_name}{note}",
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


def cost_summary(detail: dict, note: str = "") -> str:
    """成本文本摘要（用途同 price_summary）。"""
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
        f"【成本】{item_name_cn}{note}",
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


def _sender_qq(event: AstrMessageEvent) -> int | None:
    """直接取发送者 id 作为 QQ 号（OneBot/aiocqhttp 等平台即真实 QQ 号）。

    非数字身份（QQ 官方机器人的 openid）返回 None：这类平台拿不到 QQ 号，
    需要 QQ 号的功能会提示不可用。
    """
    sender_id = str(event.get_sender_id() or "").strip()
    return int(sender_id) if sender_id.isdigit() else None


async def send_mention_text(event: AstrMessageEvent, text: str) -> bool:
    """单独发送一条「@ 发送者 + 文本」的消息，返回是否发送成功。

    QQ 官方机器人只解析文本 / markdown 消息里的 @ 标签
    （适配器会把 At 组件序列化成 ``<qqbot-at-user id="..." />``），
    而带图的消息会走富媒体消息（msg_type=7），其中的 @ 标签不会被解析、
    会原样显示在群聊里。所以出图前先单独发一条文本消息承担 @。
    """
    sender_id = event.get_sender_id()
    if not sender_id:
        return False

    try:
        await event.send(
            MessageChain(
                [
                    At(qq=sender_id, name=event.get_sender_name()),
                    Plain("\n" + text),
                ]
            )
        )
        return True
    except Exception as e:
        logger.warning(f"发送 @ 摘要消息失败（不影响后续出图）: {e}")
        return False


async def prepare_image_reply(
    event: AstrMessageEvent, chain: list, summary: str
) -> list:
    """QQ 官方机器人出图前的处理：先 @ 发送者，并保证图片消息里不再出现 @ 标签。

    非 QQ 官方平台、以及私聊（C2C）原样返回：@ 仍交给 AstrBot 的
    「回复时 @ 用户」设置处理。
    """
    if event.get_platform_name() not in QQ_OFFICIAL_PLATFORMS:
        return chain
    if event.is_private_chat():
        return chain

    await send_mention_text(event, summary)

    # AstrBot 发送前的装饰阶段只给「全是 Plain/Image」的消息链补 At/Reply
    # （见 astrbot/core/pipeline/result_decorate/stage.py 的 can_decorate），
    # 这里插入一个空 Reply 占位即可跳过该装饰；QQ 官方适配器会忽略空 Reply，
    # 于是图片消息里不会再冒出原样显示的 @ 标签。
    return [Reply(id=""), *chain]


class Event:
    config = None

    @staticmethod
    def _mcp_url() -> str:
        """MCP 服务地址（config: mcp_url，默认本机 9000 端口）。"""
        try:
            url = Event.config.get("mcp_url")
        except Exception:
            url = None
        return (url or DEFAULT_MCP_URL).strip()

    @staticmethod
    async def _fetch_price(event: AstrMessageEvent, item_name: str, market_zone: str):
        """通过 MCP 取单个物品的价格详情。"""
        async with KahunaMcpClient(Event._mcp_url()) as client:
            return await client.call_tool(
                "market_price_detail",
                {"type_name": item_name, "market_zone": market_zone},
            )

    @staticmethod
    async def _reply_price(
        event: AstrMessageEvent,
        res_json: dict,
        item_name: str,
        quantity: int,
        source: str,
    ):
        """把价格结果渲染成图片（失败降级为文本）。"""
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
            return event.plain_result(
                price_summary(
                    data, quantity, source, note="（图片渲染不可用，以下为文本结果）"
                )
            )

        chain = [Image.fromFileSystem(res_path)]
        if quantity > 1:
            quantity_str += "--------总计--------\n"
            min_sell = data.get("sell", 0)
            max_buy = data.get("buy", 0)
            mid_price = data.get("mid", 0)
            quantity_str += f"sell: {min_sell * quantity:,}\n"
            quantity_str += f"buy: {max_buy * quantity:,}\n"
            quantity_str += f"mid: {mid_price * quantity:,}\n"
            chain += [Plain(quantity_str)]
        # QQ 官方机器人：图片消息承载不了 @，先单独发一条带 @ 的摘要
        chain = await prepare_image_reply(
            event, chain, price_summary(data, quantity, source)
        )
        return event.chain_result(chain)

    @staticmethod
    async def oprice(event: AstrMessageEvent, require_str: str):
        message_str = event.get_message_str()
        if message_str.split(" ")[-1].isdigit():
            quantity = int(message_str.split(" ")[-1])
            item_name = " ".join(message_str.split(" ")[1:-1])
        else:
            item_name = require_str
            quantity = 1

        # 查价走 MCP（/api/astrbot 那套转发已日落淘汰）
        try:
            res_json = await Event._fetch_price(event, item_name, "jita")
        except (McpError, ApiError) as e:
            logger.error(f"价格接口请求异常: {e}")
            return event.plain_result(api_error_for_user("价格接口"))
        except Exception as e:
            logger.error(f"价格接口请求异常: {e}")
            return event.plain_result(api_error_for_user("价格接口"))

        if not res_json.get("is_price", False):
            fuzz_list = res_json.get("data", []) or []
            if not fuzz_list:
                return event.plain_result(f"物品 {item_name} 未找到。")
            fuzz_reply = f"物品 {item_name} 不存在于数据库\n你是否在寻找:\n"
            fuzz_reply += "\n".join(fuzz_list)
            return event.plain_result(fuzz_reply)

        return await Event._reply_price(
            event, res_json, item_name, quantity, "Jita"
        )

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
            res_json = await Event._fetch_price(event, item_name, "frt")
        except (McpError, ApiError) as e:
            logger.error(f"FRT价格接口请求异常: {e}")
            return event.plain_result(api_error_for_user("FRT 价格接口"))
        except Exception as e:
            logger.error(f"FRT价格接口请求异常: {e}")
            return event.plain_result(api_error_for_user("FRT 价格接口"))

        if not res_json.get("is_price", False):
            fuzz_list = res_json.get("data", []) or []
            if not fuzz_list:
                return event.plain_result(f"物品 {item_name} 未找到。")
            fuzz_reply = f"物品 {item_name} 不存在于数据库\n你是否在寻找:\n"
            fuzz_reply += "\n".join(fuzz_list)
            return event.plain_result(fuzz_reply)

        return await Event._reply_price(
            event, res_json, item_name, quantity, "FRT"
        )

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

                plan = plan_name or Event.config["cost_plan"]
                # 成本缓存作用域由后端按 QQ 绑定解析；取不到 QQ 时用公共作用域
                arguments = {"type_name": type_name, "plan_name": plan}
                qq = _sender_qq(event)
                if qq is not None:
                    arguments["qq"] = qq

                try:
                    async with KahunaMcpClient(Event._mcp_url()) as client:
                        res_json = await client.call_tool(
                            "market_type_cost", arguments
                        )
                except (McpError, ApiError) as e:
                    logger.error(f"成本接口请求异常: {e}")
                    return event.plain_result(api_error_for_user("成本接口"))
                except Exception as e:
                    logger.error(f"成本接口请求异常: {e}")
                    return event.plain_result(api_error_for_user("成本接口"))

                if not res_json.get("is_cost", False):
                    return event.plain_result(f"物品 {type_name} 未找到成本数据。")

                detail_dict = res_json.get("data", {}) or {}
                try:
                    pic_path = await render_single_cost_pic(detail_dict)
                except Exception as e:
                    logger.error(f"渲染成本图片失败: {e}")
                    return event.plain_result(
                        cost_summary(
                            detail_dict, note="（图片渲染不可用，以下为文本结果）"
                        )
                    )

                chain = [Image.fromFileSystem(pic_path)]
                # QQ 官方机器人：图片消息承载不了 @，先单独发一条带 @ 的摘要
                chain = await prepare_image_reply(
                    event, chain, cost_summary(detail_dict)
                )
                return event.chain_result(chain)
            finally:
                calculate_lock.release()
        else:
            return event.plain_result("已有成本计算进行中，请稍候再试。")

    @staticmethod
    async def bind_kahunasystem(event: AstrMessageEvent, uuid: str):
        """把当前 QQ 号绑定到 kahunasystem 账号（绑定走 HTTP，不进 MCP）。"""
        if not uuid:
            return event.plain_result("格式错误，缺少 uuid。")

        qq = _sender_qq(event)
        if qq is None:
            return event.plain_result(
                "当前平台无法获取你的 QQ 号，绑定功能暂时不可用。"
            )

        try:
            res_json = await bind_qq(
                Event.config["kahunasystem_host"],
                qq,
                uuid,
            )
        except ApiError as e:
            logger.error(f"绑定接口请求失败: {e}")
            return event.plain_result("绑定失败，请稍后再试。")
        except Exception as e:
            logger.error(f"绑定调用异常: {e}")
            return event.plain_result("绑定失败，请稍后再试。")

        status = res_json.get("status")
        try:
            status = int(status)
        except Exception:
            status = None
        if status and status >= 400:
            return event.plain_result(
                clip_text(res_json.get("message") or "绑定失败。", 120)
            )

        return event.plain_result("绑定成功")

    @staticmethod
    async def get_my_qq_for_llm(event: AstrMessageEvent) -> str:
        """给 LLM 工具用：返回当前用户真实 QQ 号，或不可用的说明。

        永不返回 None：AstrBot 中 LLM 工具返回 None 表示「结果不进下一轮 prompt」，
        那样模型就看不到 QQ 号了。
        """
        qq = _sender_qq(event)
        if qq is not None:
            return (
                f"当前用户的真实 QQ 号是 {qq}。"
                "调用需要 qq 参数的 kahunasystem 工具时，请直接使用该值。"
            )
        return (
            "无法获取当前用户的真实 QQ 号（当前平台只提供 openid）。"
            "在拿到 QQ 号之前，不要调用任何需要 qq 参数的 kahunasystem 工具，"
            "也不要猜测 QQ 号。"
        )
