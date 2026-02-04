# import logger
import json

import aiohttp
import imgkit
import os
import sys
import jinja2  # 添加Jinja2导入
import requests
import base64
import asyncio
from datetime import datetime, timedelta
from pyppeteer import launch
import math

from astrbot.core import logger

# 临时文件目录
TMP_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../tmp"))
# 资源目录
RESOURCE_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../resource"))

# 模板目录
template_path = os.path.join(RESOURCE_PATH, "templates")
# CSS目录
css_path = os.path.join(RESOURCE_PATH, "css")

def format_number(value):
    """将数字格式化为带千位分隔符的字符串"""
    try:
        # 转换为浮点数
        num = float(value)
        # 如果是整数，不显示小数部分
        if num.is_integer():
            return "{:,}".format(int(num))
        # 否则保留两位小数
        return "{:,.2f}".format(num)
    except (ValueError, TypeError):
        # 如果无法转换为数字，返回原值
        return value

# 方法2：直接添加到环境
def round_filter(value, precision=2):
    try:
        return round(float(value), precision)
    except (ValueError, TypeError):
        return value

class PictureRender():
    @classmethod
    def check_tmp_dir(cls):
        # 确保临时目录存在
        if not os.path.exists(TMP_PATH):
            os.makedirs(TMP_PATH)

    @classmethod
    async def render_pic(cls, output_path: str, html_content: str, width: int = 800, height: int = 800, wait_time: int = 5):
        # 将HTML内容保存到临时文件
        html_file_path = os.path.join(TMP_PATH, "temp_render.html")
        with open(html_file_path, 'w', encoding='utf-8') as f:
            f.write(html_content)

        launch_a = [
            '--no-sandbox',
            '--disable-setuid-sandbox',
            '--disable-dev-shm-usage',
            '--disable-gpu',  # 禁用GPU加速
        ]

        proxy_arg = []

        # 检查是否为 Linux 系统
        if sys.platform.startswith('linux'):
        # 启动浏览器，添加必要的参数以确保在Linux环境下正常运行
            browser = await launch(
                headless=True,
                args=launch_a + proxy_arg
            )

        else:
            browser = await launch(
                executablePath=r'C:\Program Files\Google\Chrome\Application\chrome.exe',
                headless=True,
                args=proxy_arg
            )

        page = await browser.newPage()
        await page.setViewport({'width': width, 'height': height})

        try:
            # 设置页面内容
            await page.setContent(html_content)

            # 等待字体加载完成
            await page.waitForFunction('document.fonts.ready', {'timeout': wait_time * 1000})

            # 检查是否有Chart.js图表，如果有则等待图表渲染完成
            has_chart = await page.evaluate('typeof Chart !== "undefined" && document.getElementById("costChart") !== null')
            if has_chart:
                # 禁用Chart.js动画以加速渲染
                await page.evaluate('''
                    if (typeof Chart !== "undefined") {
                        Chart.defaults.animation = false;
                        // 添加一个全局标志，表示图表渲染完成
                        window.chartRendered = false;
                        const originalDraw = Chart.prototype.draw;
                        Chart.prototype.draw = function() {
                            originalDraw.apply(this, arguments);
                            window.chartRendered = true;
                        };
                    }
                ''')

                # 等待图表渲染完成或超时
                try:
                    await page.waitForFunction('window.chartRendered === true', {'timeout': wait_time * 1000})
                except Exception as e:
                    logger.warning(f"等待图表渲染超时: {e}，使用备用等待时间")
                    await asyncio.sleep(wait_time)  # 备用等待机制
            else:
                # 如果没有图表，等待DOM内容加载完成
                await page.waitForFunction('document.readyState === "complete"')
                # 额外等待一小段时间确保CSS渲染完成
                await asyncio.sleep(1)

            # 截图
            await page.screenshot({'path': output_path, 'fullPage': True})
        except Exception as e:
            logger.error(f"渲染过程发生错误: {e}")
            # 记录更详细的错误信息
            logger.error(f"详细错误: {str(e)}")
            logger.error(f"错误类型: {type(e).__name__}")
        finally:
            try:
                if 'browser' in locals() and browser:
                    await browser.close()
            except Exception as close_error:
                logger.error(f"关闭浏览器时发生错误: {close_error}")

        return output_path


    @classmethod
    async def get_eve_item_icon_base64(cls, type_id: int):
        item_image_path = await cls.download_eve_item_image(type_id)  # 这里的ID需要根据实际物品ID修改
        item_image_base64 = cls.get_image_base64(item_image_path) if item_image_path else None

        return item_image_base64

    @classmethod
    async def get_character_portrait_base64(cls, character_id: int):
        portrait_image_path = await cls.download_character_protrait(character_id)
        portrait_image_base64 = cls.get_image_base64(portrait_image_path)

        return portrait_image_base64

    @classmethod
    async def download_eve_item_image(cls, type_id: int, size: int = 64) -> str:
        """
        下载EVE物品图片
        :param type_id: 物品ID
        :param size: 图片尺寸，可选值：64, 1024
        :return: 图片本地路径
        """
        # 创建图片存储目录
        image_path = os.path.join(RESOURCE_PATH, "img")
        if not os.path.exists(image_path):
            os.makedirs(image_path)

        # 构建图片URL和本地保存路径
        local_path = os.path.join(image_path, f"item_{type_id}_{size}.png")

        # 如果图片已存在，直接返回路径
        if os.path.exists(local_path):
            return local_path

        # 尝试从主URL下载（现在使用原来的备用URL作为主URL）
        try:
            # 主URL（原备用URL）
            url = f"https://imageserver.eveonline.com/Type/{type_id}_{size}.png"
            logger.info(f"尝试从主URL下载: {url}")

            # 下载图片，禁用SSL验证
            async with aiohttp.ClientSession() as session:
                async with session.get(url, ssl=False, timeout=aiohttp.ClientTimeout(total=10)) as response:
                    if response.status == 200:
                        content = await response.read()
                        # 保存图片
                        with open(local_path, 'wb') as f:
                            f.write(content)

                        logger.info(f"成功下载物品图片: {type_id}")
                        return local_path
                    else:
                        raise Exception(f"请求状态码: {response.status}")
        except Exception as e:
            logger.error(f"从主URL下载EVE物品图片失败: {e}")

            # 尝试备用URL（原主URL）
            try:
                # 备用URL
                backup_url = f"https://images.evetech.net/types/{type_id}/icon?size={size}"
                logger.info(f"尝试从备用URL下载: {backup_url}")

                async with aiohttp.ClientSession() as session:
                    async with session.get(backup_url, ssl=False, timeout=aiohttp.ClientTimeout(total=10)) as response:
                        if response.status == 200:
                            content = await response.read()
                            with open(local_path, 'wb') as f:
                                f.write(content)

                            logger.info(f"从备用URL成功下载物品图片: {type_id}")
                            return local_path
                        else:
                            raise Exception(f"备用URL请求状态码: {response.status}")

            except Exception as backup_e:
                logger.error(f"从备用URL下载EVE物品图片也失败: {backup_e}")

            # 如果两个URL都失败，返回默认图片路径
            default_image = os.path.join(RESOURCE_PATH, "img", "default_item.png")

            # 如果默认图片不存在，创建一个简单的默认图片
            if not os.path.exists(default_image):
                try:
                    # 创建一个简单的1x1像素透明PNG
                    with open(default_image, 'wb') as f:
                        f.write(base64.b64decode(
                            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="))
                except Exception:
                    logger.error("无法创建默认图片")
                    return None

            return default_image

    @classmethod
    def get_image_base64(cls, image_path: str) -> str:
        """将图片转换为base64编码"""
        try:
            with open(image_path, 'rb') as f:
                image_data = f.read()
                return base64.b64encode(image_data).decode('utf-8')
        except Exception as e:
            logger.error(f"图片转base64失败: {e}")
            return None

    # @classmethod
    # async def download_character_protrait(cls, character_id: int):
    #     # 创建图片存储目录
    #     image_path = os.path.join(RESOURCE_PATH, "img")
    #     if not os.path.exists(image_path):
    #         os.makedirs(image_path)

    #     # 构建图片URL和本地保存路径
    #     local_path = os.path.join(image_path, f"portrait_{character_id}.png")

    #     # 如果图片已存在，直接返回路径
    #     if os.path.exists(local_path):
    #         return local_path

    #     image_data = await eveesi.characters_character_portrait(character_id)
    #     px64_url = image_data['px64x64']

    #     # 下载图片，禁用SSL验证
    #     async with aiohttp.ClientSession() as session:
    #         async with session.get(px64_url, ssl=False, timeout=aiohttp.ClientTimeout(total=10)) as response:
    #             if response.status == 200:
    #                 content = await response.read()
    #                 # 保存图片
    #                 with open(local_path, 'wb') as f:
    #                     f.write(content)

    #                 logger.info(f"成功下载角色头像: {character_id}")
    #                 return local_path
    #             else:
    #                 raise Exception(f"请求状态码: {response.status}")

    #     return local_path
