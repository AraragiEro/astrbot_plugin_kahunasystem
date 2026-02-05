import aiohttp


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


async def api_price_detail(host: str, type_name: str):
    return await post_json(
        host,
        "/api/astrbot/market/price_detail",
        {"type_name": type_name},
        timeout=10,
    )


async def api_type_cost(host: str, type_name: str, user_name: str, plan_name: str):
    return await post_json(
        host,
        "/api/astrbot/market/type_cost",
        {
            "type_name": type_name,
            "user_name": user_name,
            "plan_name": plan_name,
        },
        timeout=120,
    )


async def api_fuzz_type_name(host: str, type_name: str):
    return await post_json(
        host,
        "/api/astrbot/market/fuzz_type_name",
        {"type_name": type_name},
        timeout=10,
    )
