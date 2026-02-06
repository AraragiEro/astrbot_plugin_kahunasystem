from astrbot.api.event import filter, AstrMessageEvent
from astrbot.core import logger

from .api_client import api_qq_vip_state
from .event import Event


def _normalize_bool(value):
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "t"}
    return False


def _extract_vip_flags(payload: dict):
    if not isinstance(payload, dict):
        return False, False
    data = payload.get("data")
    if isinstance(data, dict):
        payload = {**payload, **data}
    vip_level = payload.get("vipLevel") or payload.get("vip_level")
    vip_level_code = payload.get("vipLevelCode") or payload.get("vip_level_code")
    if isinstance(vip_level, str) or isinstance(vip_level_code, str):
        level = (vip_level or "").strip().lower()
        code = (vip_level_code or "").strip().lower()
        if code == "vip_omega" or level == "omega":
            return True, True
        if code == "vip_alpha" or level == "alpha":
            return True, False
        if level == "free":
            return False, False
    alpha = payload.get("alpha", payload.get("is_alpha", payload.get("vip_alpha")))
    omega = payload.get("omega", payload.get("is_omega", payload.get("vip_omega")))
    if alpha is None and omega is None:
        vip_state = payload.get("vip_state") or payload.get("state")
        if isinstance(vip_state, str):
            state = vip_state.strip().lower()
            return state in {"alpha", "omega"}, state == "omega"
        if isinstance(vip_state, (int, float)):
            if vip_state >= 2:
                return True, True
            if vip_state == 1:
                return True, False
            return False, False
        return False, False
    return _normalize_bool(alpha), _normalize_bool(omega)


def _is_white_session(event: AstrMessageEvent) -> bool:
    session_ids = (Event.config or {}).get("vip_white_session_ids") or []
    if not isinstance(session_ids, (list, tuple, set)):
        return False
    session_id = event.get_session_id()
    return session_id is not None and str(session_id) in {str(item) for item in session_ids}


class AlphaFilter(filter.CustomFilter):
    async def filter(self, event: AstrMessageEvent):
        qq = event.get_sender_id()
        # 通过 API 获取用户状态，alpha 或 omega 为 True，否则为 False
        if _is_white_session(event):
            return True

        try:
            res_json = await api_qq_vip_state(Event.config["kahunasystem_host"], qq)
        except Exception as e:
            logger.error(f"VIP 状态接口请求异常: {e}")
            return False

        status = res_json.get("status")
        try:
            status = int(status)
        except Exception:
            status = None
        if status and status >= 400:
            logger.error(f"VIP 状态接口返回错误: {res_json.get('message')}")
            return False

        alpha, omega = _extract_vip_flags(res_json)
        return alpha or omega


class OmegaFilter(filter.CustomFilter):
    async def filter(self, event: AstrMessageEvent):
        qq = event.get_sender_id()
        # 通过 API 获取用户状态，omega 为 True，否则为 False
        if _is_white_session(event):
            return True

        try:
            res_json = await api_qq_vip_state(Event.config["kahunasystem_host"], qq)
        except Exception as e:
            logger.error(f"VIP 状态接口请求异常: {e}")
            return False

        status = res_json.get("status")
        try:
            status = int(status)
        except Exception:
            status = None
        if status and status >= 400:
            logger.error(f"VIP 状态接口返回错误: {res_json.get('message')}")
            return False

        _, omega = _extract_vip_flags(res_json)
        return omega