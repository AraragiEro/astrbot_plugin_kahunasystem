from .api_client import api_esi_name2id
from astrbot.core import logger

async def get_id(ntype: str, name: str):
    if not name:
        logger.warning("get_id: name is empty")
        return None
    try:
        res_json = await api_esi_name2id(
            name
        )
    except Exception as e:
        logger.error(f"模糊查询接口请求异常: {e}")
        return None
    if ntype not in res_json:
        logger.warning(f"搜索 ''{name}'' 结果为空")
        return None
    return res_json[ntype][0]["id"]

async def get_character_id(character_name: str):
    return await get_id("characters", character_name)

async def get_corporation_id(corporation_name: str):
    return await get_id("corporations", corporation_name)

async def get_system_id(system_name: str):
    return await get_id("systems", system_name)