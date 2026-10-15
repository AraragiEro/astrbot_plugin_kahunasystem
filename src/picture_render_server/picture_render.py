# import logger
import json

import aiohttp
import os
import sys
import jinja2  # 添加Jinja2导入
import requests
import base64
import asyncio
from datetime import datetime, timedelta
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
# JS目录
js_path = os.path.join(RESOURCE_PATH, "js")

# 需要本地化的外部 CDN 资源（避免渲染时因网络加载超时）
JS_RESOURCES = {
    'https://cdn.jsdmirror.com/npm/@tailwindcss/browser@4': 'tailwindcss-browser.js',
    'https://cdn.jsdmirror.com/npm/chart.js': 'chart.js',
    'https://cdn.jsdmirror.com/npm/chartjs-adapter-date-fns': 'chartjs-adapter-date-fns.js',
    'https://d3js.org/d3.v7.min.js': 'd3.v7.min.js',
    'https://unpkg.com/d3-sankey@0.12.3/dist/d3-sankey.min.js': 'd3-sankey.min.js',
}

# AstrBot 内置的文转图渲染器（context.html_renderer）由 main.py 在插件初始化时注入。
# 走远程渲染可以完全避开「核心依赖版本保护」：插件不需要在本地安装浏览器依赖。
_t2i_renderer = None
_t2i_endpoint = None


def set_t2i_renderer(renderer, endpoint: str | None = None) -> None:
    """注入 AstrBot 的文转图渲染器与自定义端点。

    :param renderer: ``context.html_renderer``，为空表示当前环境不支持远程渲染
    :param endpoint: AstrBot 配置项 ``t2i_endpoint``，为空时使用官方端点池
    """
    global _t2i_renderer, _t2i_endpoint
    _t2i_renderer = renderer
    _t2i_endpoint = endpoint or None


def get_t2i_renderer():
    """返回已注入的文转图渲染器，未注入时为 None。"""
    return _t2i_renderer


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
    async def _ensure_local_js_resources(cls):
        """将外部 CDN JS 资源异步下载到本地，避免渲染时因网络加载超时。"""
        if not os.path.exists(js_path):
            os.makedirs(js_path)
        for cdn_url, filename in JS_RESOURCES.items():
            local_path = os.path.join(js_path, filename)
            if os.path.exists(local_path):
                continue
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(cdn_url, ssl=False, timeout=aiohttp.ClientTimeout(total=30)) as response:
                        if response.status == 200:
                            content = await response.read()
                            with open(local_path, 'wb') as f:
                                f.write(content)
                            logger.info(f"成功缓存 JS 资源: {filename}")
                        else:
                            logger.warning(f"下载 JS 资源失败 {cdn_url}: HTTP {response.status}")
            except Exception as e:
                logger.warning(f"下载 JS 资源异常 {cdn_url}: {e}")

    @classmethod
    def _replace_cdn_with_local(cls, html_content: str) -> str:
        """将 HTML 中的外部 <script src> 替换为内联 <script>，避免 setContent 后网络加载失败。"""
        for cdn_url, filename in JS_RESOURCES.items():
            local_path = os.path.join(js_path, filename)
            if not os.path.exists(local_path):
                continue
            try:
                with open(local_path, 'r', encoding='utf-8') as f:
                    js_content = f.read()
                old_tag = f'<script src="{cdn_url}"></script>'
                new_tag = f'<script>{js_content}</script>'
                if old_tag in html_content:
                    html_content = html_content.replace(old_tag, new_tag)
                    logger.debug(f"已内联 JS: {filename}")
            except Exception as e:
                logger.warning(f"内联 JS 失败 {filename}: {e}")
        return html_content

    @staticmethod
    def _is_image_file(path: str) -> bool:
        """校验落盘文件确实是图片。

        远程渲染失败时（例如端点返回 403/500 的错误页）AstrBot 的下载逻辑
        仍会把响应体写成 .jpg，因此这里按文件头判断，避免把错误页当图片发出去。
        """
        try:
            with open(path, 'rb') as f:
                head = f.read(8)
        except OSError:
            return False
        return head.startswith(b'\xff\xd8') or head.startswith(b'\x89PNG')

    @staticmethod
    def crop_to_width(path: str, target_width: int, align: str = "center") -> str:
        """把渲染结果裁到目标宽度。

        AstrBot 文转图服务固定用 800px 视口渲染，且忽略 viewport 选项，
        因此本地按内容对齐方式裁掉多余背景（Pillow 随 AstrBot 核心提供）：
        - ``align="center"``：内容居中（价格模板 ``max-w-lg`` + ``mx-auto``）
        - ``align="left"``：内容靠左（成本模板 ``min-width: 526px``）

        Pillow 缺失、图片本身不宽于目标、或裁剪失败时，原样返回不阻塞出图。
        """
        try:
            from PIL import Image as PILImage
        except ImportError:
            return path

        try:
            with PILImage.open(path) as img:
                if img.width <= target_width:
                    return path
                left = 0 if align == "left" else (img.width - target_width) // 2
                cropped = img.crop((left, 0, left + target_width, img.height))
                if cropped.mode not in ("RGB", "L"):
                    cropped = cropped.convert("RGB")
            # 文件句柄已关闭后再写回，避免 Windows 上的占用冲突
            cropped.save(path, quality=90)
            return path
        except Exception as e:
            logger.warning(f"裁剪图片宽度失败，改用原图: {e}")
            return path

    @classmethod
    async def render_pic_remote(cls, html_content: str, options: dict | None = None) -> str:
        """通过 AstrBot 内置的文转图服务渲染 HTML，返回本地图片路径。

        HTML 已由本地 Jinja2 渲染完成，因此整段包进 ``{% raw %}``，
        防止服务端 Jinja2 二次解析模板里 JS/CSS 的花括号。

        渲染失败时抛出异常，由上层决定回退到本地 pyppeteer 或纯文本。
        """
        if _t2i_renderer is None:
            raise RuntimeError("AstrBot 文转图渲染器不可用（context.html_renderer 缺失）")

        # 截图选项遵循 Playwright screenshot API（AstrBot 文档）；禁用 CSS 动画避免截到中间帧
        render_options = {
            "full_page": True,
            "type": "jpeg",
            "quality": 80,
            "animations": "disabled",
        }
        if options:
            render_options.update(options)

        pic_path = await _t2i_renderer.render_custom_template(
            "{% raw %}" + html_content + "{% endraw %}",
            {},
            return_url=False,
            options=render_options,
            endpoint=_t2i_endpoint,
        )
        if not pic_path or not cls._is_image_file(pic_path):
            raise RuntimeError(f"文转图服务未返回有效图片: {pic_path}")
        return pic_path

    @classmethod
    def _load_launch(cls):
        """惰性获取 pyppeteer 的 launch。

        AstrBot v4.29+ 启用了核心依赖版本保护：核心要求 websockets>=15，而
        pyppeteer 2.0.0 依赖 websockets<11，二者无法安装在同一 Python 环境，
        因此渲染依赖改为运行时加载，缺失时抛出明确异常，由上层降级为纯文本返回。
        """
        try:
            from pyppeteer import launch
        except ImportError as exc:
            raise RuntimeError(
                "pyppeteer 不可用，当前环境无法渲染图片（AstrBot 核心依赖保护与 pyppeteer 冲突）"
            ) from exc
        return launch

    @classmethod
    async def render_pic(cls, output_path: str, html_content: str, width: int = 800, height: int = 800, wait_time: int = 30):
        # 先确保外部 JS 资源已缓存到本地，避免渲染时因网络加载超时
        await cls._ensure_local_js_resources()
        html_content = cls._replace_cdn_with_local(html_content)

        # 惰性加载渲染依赖，缺失时抛出明确异常（由上层降级为纯文本）
        launch = cls._load_launch()

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
            # 设置页面内容（旧版 pyppeteer 的 setContent 不支持 options 参数）
            await page.setContent(html_content)

            # 字体加载为非关键步骤，改用固定等待避免 waitForFunction 超时
            await asyncio.sleep(2)

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
                    await page.waitForFunction('window.chartRendered === true')
                except Exception as e:
                    logger.warning(f"等待图表渲染超时: {e}，使用备用等待时间")
                    await asyncio.sleep(wait_time)  # 备用等待机制
            else:
                # 如果没有图表，固定等待确保DOM和CSS渲染完成
                await asyncio.sleep(min(wait_time, 10))

            # 截图
            await page.screenshot({'path': output_path, 'fullPage': True})
            return output_path
        except Exception as e:
            logger.error(f"渲染过程发生错误: {e}")
            # 记录更详细的错误信息
            logger.error(f"详细错误: {str(e)}")
            logger.error(f"错误类型: {type(e).__name__}")
            return None
        finally:
            try:
                if 'browser' in locals() and browser:
                    await browser.close()
            except Exception as close_error:
                logger.error(f"关闭浏览器时发生错误: {close_error}")


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
