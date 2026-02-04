# import logger
import json

import aiohttp
import imgkit
import os
import sys
import jinja2  # 娣诲姞Jinja2瀵煎叆
import requests
import base64
import asyncio
from datetime import datetime, timedelta
from pyppeteer import launch
import math
from astrbot.core import logger

from .picture_render import PictureRender, TMP_PATH, template_path

def format_number(value):
    """灏嗘暟瀛楁牸寮忓寲涓哄甫鍗冧綅鍒嗛殧绗︾殑瀛楃涓?"""
    try:
        # 杞崲涓烘诞鐐规暟
        num = float(value)
        # 濡傛灉鏄暣鏁帮紝涓嶆樉绀哄皬鏁伴儴鍒?
        if num.is_integer():
            return "{:,}".format(int(num))
        # 鍚﹀垯淇濈暀涓や綅灏忔暟
        return "{:,.2f}".format(num)
    except (ValueError, TypeError):
        # 濡傛灉鏃犳硶杞崲涓烘暟瀛楋紝杩斿洖鍘熷€?
        return value

async def render_price_res_pic(item_data: dict, history_data: list, order_data: dict):
    # 价格数据均来自 API
    max_buy = item_data.get("buy", 0)
    mid_price = item_data.get("mid", 0)
    min_sell = item_data.get("sell", 0)

    # API 应返回 name/name_zh/matched_name；若缺失则回退到 type_id 字符串
    item_name = (
        item_data.get("name_zh")
        or item_data.get("matched_name")
        or item_data.get("name")
        or str(item_data.get("type_id", ""))
    )

    PictureRender.check_tmp_dir()

    # 鑾峰彇Jinja2鐜
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(template_path),
        autoescape=jinja2.select_autoescape(['html', 'xml'])
    )

    # 鏍规嵁鏄惁鏈夋ā绯婂尮閰嶇粨鏋滈€夋嫨妯℃澘
    try:
        # API 应返回 type_id；若缺失则无法下载图标
        item_id = item_data.get("type_id")
        if item_id:
            item_image_path = await PictureRender.download_eve_item_image(item_id)
            item_image_base64 = PictureRender.get_image_base64(item_image_path) if item_image_path else None
        else:
            item_image_base64 = None

        env.filters['format_number'] = format_number
        # 鍋囷?/order_data 为 API 返回的订单数据
        order_data = order_data or {"buy_order": {}, "sell_order": {}}
        buy_orders = [[k, v] for k, v in order_data.get('buy_order', {}).items()]
        buy_orders.sort(key=lambda x: x[1]['price'], reverse=True)
        sell_orders = [[k, v] for k, v in order_data.get('sell_order', {}).items()]
        sell_orders.sort(key=lambda x: x[1]['price'])
        template = env.get_template('price_template.j2')
        html_content = template.render(
            item_name=item_name,
            max_buy=f"{max_buy:,.2f}",
            mid_price=f"{mid_price:,.2f}",
            min_sell=f"{min_sell:,.2f}",
            item_image_base64=item_image_base64,
            sell_orders=sell_orders,
            buy_orders=buy_orders,
            price_history=history_data
        )
    except jinja2.exceptions.TemplateNotFound as e:
        logger.error(f"渲染模板失败: {e}")
        logger.error(f"请确保模板文件已放置在 {template_path} 目录下")
        return None

    # 鐢熸垚杈撳嚭璺緞
    output_path = os.path.abspath(os.path.join((TMP_PATH), "price_res.jpg"))

    # 澧炲姞绛夊緟鏃堕棿鍒?绉掞紝纭繚鍥捐〃鏈夎冻澶熸椂闂存覆鏌?
    pic_path = await PictureRender.render_pic(output_path, html_content, width=550, height=720, wait_time=120)

    if not pic_path:
        raise Exception("pic_path not exist.")
    return pic_path

async def render_single_cost_pic(single_cost_data: dict):
    """
    渲染成本详情图。数据来源为 API 返回的 single_cost_data。
    """
    PictureRender.check_tmp_dir()

    # API 应返回 item_name/item_name_cn；若缺失则回退为 type_id 字符串
    item_id = single_cost_data.get("type_id")
    item_name = single_cost_data.get("item_name") or str(item_id or "")
    item_name_cn = single_cost_data.get("item_name_cn") or item_name
    user_name = single_cost_data.get("user_name")
    plan_name = single_cost_data.get("plan_name")

    # 成本与 JITA 价格
    cost = single_cost_data.get("total_cost", 0)
    market_detail = single_cost_data.get("market_detail", []) or []
    jita_buy = market_detail[0] if len(market_detail) > 0 else 0
    jita_mid = market_detail[1] if len(market_detail) > 1 else 0
    jita_sell = market_detail[2] if len(market_detail) > 2 else 0

    # 计算利润与利润率
    profit = jita_sell - cost
    profit_rate = profit / cost if cost else 0

    # 成本构成
    group_detail = single_cost_data.get("group_detail", {}) or {}
    eiv = single_cost_data.get("eiv", []) or []
    group_cost_list = [[group, data[0], data[1]] for group, data in group_detail.items()]
    group_cost_list.sort(key=lambda x: x[1], reverse=True)
    cost_components = [
        {
            'name': group_data[0],
            'value': group_data[1],
        }
        for group_data in group_cost_list
    ]
    if len(eiv) > 0:
        cost_components.append(
            {
                'name': '系数',
                'value': eiv[0]
            }
        )

    # 获取 Jinja2 环境并渲染
    try:
        env = jinja2.Environment(
            loader=jinja2.FileSystemLoader(template_path),
            autoescape=jinja2.select_autoescape(['html', 'xml'])
        )
        env.filters['format_number'] = format_number
        template = env.get_template('single_cost.j2')
        html_content = template.render(
            item_name=item_name,
            item_name_cn=item_name_cn,
            item_id=item_id,
            user_name=user_name,
            plan_name=plan_name,
            jita_buy=jita_buy,
            jita_mid=jita_mid,
            jita_sell=jita_sell,
            cost=cost,
            profit=profit,
            profit_rate=profit_rate,
            cost_components=cost_components,
            footer_text=single_cost_data.get("footer_text")
        )
    except jinja2.exceptions.TemplateNotFound as e:
        logger.error(f"渲染模板失败: {e}")
        logger.error(f"请确保模板文件已放置在 {template_path} 目录下")
        return None

    output_path = os.path.abspath(os.path.join((TMP_PATH), "single_cost_res.jpg"))
    pic_path = await PictureRender.render_pic(output_path, html_content, width=550, height=720, wait_time=120)
    if not pic_path:
        raise Exception("pic_path not exist.")
    return pic_path
