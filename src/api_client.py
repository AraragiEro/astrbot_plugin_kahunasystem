import json
import aiohttp
from astrbot.api import logger

# 服务间认证密钥：由 main.py 在插件初始化时通过 set_service_api_key() 注入。
# kahunabot 无法对 QQ 机器人做交互式登录，因此改用共享服务密钥（请求头 X-Internal-API-Key）。
_SERVICE_API_KEY = ""


def set_service_api_key(api_key: str):
    """注入服务间认证密钥（应与 kahunabot config.toml 的 [SERVICE] API_KEY 一致）"""
    global _SERVICE_API_KEY
    _SERVICE_API_KEY = (api_key or "").strip()


def _build_headers() -> dict:
    """构造请求头；未配置密钥时不携带该头（服务端将按未授权处理）"""
    if _SERVICE_API_KEY:
        return {"X-Internal-API-Key": _SERVICE_API_KEY}
    return {}


async def get_json(host: str, path: str, timeout: int):
    if host.startswith("http://") or host.startswith("https://"):
        api_url = f"{host}{path}"
    else:
        api_url = f"http://{host}{path}"

    async with aiohttp.ClientSession() as session:
        async with session.get(
            api_url,
            headers=_build_headers(),
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            if resp.status != 200:
                raise ValueError(f"HTTP {resp.status}")
            return await resp.json()


async def post_json(host: str, path: str, payload: dict, timeout: int):
    if host.startswith("http://") or host.startswith("https://"):
        api_url = f"{host}{path}"
    else:
        api_url = f"http://{host}{path}"

    logger.debug(f"POST {api_url}, payload: {payload}")
    async with aiohttp.ClientSession() as session:
        async with session.post(
            api_url,
            json=payload,
            headers=_build_headers(),
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            if resp.status != 200:
                try:
                    body = await resp.text()
                    # 后端若返回 JSON 字符串，转为中文可读格式，避免 \uXXXX 日志乱码
                    try:
                        body_json = json.loads(body)
                        body = json.dumps(body_json, ensure_ascii=False)
                    except Exception:
                        pass
                except Exception:
                    body = resp.reason
                raise ValueError(f"HTTP {resp.status}, message: {body}")
            return await resp.json()


async def api_info(host: str, api_id: str):
    return await post_json(
        host,
        "/api/astrbot/kahunasystem/api/info",
        {"args": api_id},
        timeout=10,
    )


async def api_run(
    host: str, api_id: str, args: dict, qq: str = None, access_token: str = None
):
    return await post_json(
        host,
        "/api/astrbot/kahunasystem/api/run",
        {"api_id": api_id, "args": args, "qq": qq, "access_token": access_token},
        timeout=300,
    )


async def api_price_detail(host: str, type_name: str, access_token: str = None):
    return await api_run(
        host,
        "market_price_detail",
        {"type_name": type_name},
        None,
        access_token,
    )


async def api_frt_price_detail(host: str, type_name: str, access_token: str = None):
    return await api_run(
        host,
        "market_price_detail",
        {"type_name": type_name, "market_zone": "frt"},
        None,
        access_token,
    )


async def api_type_cost(
    host: str, type_name: str, user_name: str, plan_name: str, access_token: str = None
):
    return await api_run(
        host,
        "market_type_cost",
        {
            "type_name": type_name,
            "user_name": user_name,
            "plan_name": plan_name,
        },
        None,
        access_token,
    )


async def api_qq_bind(host: str, qq, uuid: str):
    api_url = f"http://{host}/api/astrbot/kahunasystem/qq/bind"
    async with aiohttp.ClientSession() as session:
        async with session.post(
            api_url,
            json={"QQ": qq, "uuid": uuid},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            data = None
            try:
                data = await resp.json()
            except Exception:
                data = {}
            if not isinstance(data, dict):
                data = {}
            data.setdefault("status", resp.status)
            return data


async def api_qq_vip_state(host: str, qq):
    api_url = f"http://{host}/api/astrbot/kahunasystem/qq/vip"
    async with aiohttp.ClientSession() as session:
        async with session.post(
            api_url,
            json={"QQ": qq},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            data = None
            try:
                data = await resp.json()
            except Exception:
                data = {}
            if not isinstance(data, dict):
                data = {}
            data.setdefault("status", resp.status)
            return data


async def api_zkb_get_character_data(host: str, character_id: int):
    return await get_json(
        "https://zkillboard.com/api/stats/characterID",
        f"/{character_id}/",
    )


async def api_zkb_get_corporation_data(host: str, corporation_id: int):
    return await get_json(
        "https://zkillboard.com/api/stats/corporationID",
        f"/{corporation_id}/",
    )


async def api_esi_name2id(search_str: str):
    return await post_json(
        "https://esi.evetech.net",
        "/latest/universe/ids/",
        payload=[search_str],
        timeout=10,
    )
