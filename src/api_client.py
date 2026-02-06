import aiohttp


async def get_json(host: str, path: str, timeout: int):
    api_url = f"http://{host}{path}"
    async with aiohttp.ClientSession() as session:
        async with session.get(
            api_url,
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            if resp.status != 200:
                raise ValueError(f"HTTP {resp.status}")
            return await resp.json()


async def post_json(host: str, path: str, payload: dict, timeout: int):
    api_url = f"http://{host}{path}"
    async with aiohttp.ClientSession() as session:
        async with session.post(
            api_url,
            json=payload,
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            if resp.status != 200:
                raise ValueError(f"HTTP {resp.status}")
            return await resp.json()


async def api_list(host: str):
    return await get_json(
        host,
        "/api/astrbot/kahunasystem/api/list",
        timeout=10,
    )


async def api_info(host: str, api_ids: list[str]):
    return await post_json(
        host,
        "/api/astrbot/kahunasystem/api/info",
        {"args": api_ids},
        timeout=10,
    )


async def api_run(host: str, api_id: str, args: dict):
    return await post_json(
        host,
        "/api/astrbot/kahunasystem/api/run",
        {"api_id": api_id, "args": args},
        timeout=120,
    )


async def api_price_detail(host: str, type_name: str):
    return await api_run(
        host,
        "market_price_detail",
        {"type_name": type_name},
    )


async def api_type_cost(host: str, type_name: str, user_name: str, plan_name: str):
    return await api_run(
        host,
        "market_type_cost",
        {
            "type_name": type_name,
            "user_name": user_name,
            "plan_name": plan_name,
        },
    )


async def api_fuzz_type_name(host: str, type_name: str):
    return await api_run(
        host,
        "market_fuzz_type_name",
        {"type_name": type_name},
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
