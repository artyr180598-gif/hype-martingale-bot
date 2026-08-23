import os
import json
import requests
import logging

logger = logging.getLogger(__name__)

REDIS_URL   = os.getenv("UPSTASH_REDIS_REST_URL", "")
REDIS_TOKEN = os.getenv("UPSTASH_REDIS_REST_TOKEN", "")


def _headers():
    return {"Authorization": f"Bearer {REDIS_TOKEN}"}


def redis_set(key: str, data: dict) -> bool:
    """Сохранить данные в Redis"""
    if not REDIS_URL or not REDIS_TOKEN:
        return False
    try:
        value = json.dumps(data, ensure_ascii=False)
        r = requests.post(
            f"{REDIS_URL}/set/{key}",
            headers=_headers(),
            json={"value": value},
            timeout=5
        )
        return r.status_code == 200
    except Exception as e:
        logger.error(f"Redis SET {key}: {e}")
        return False


def redis_get(key: str) -> dict | None:
    """Получить данные из Redis"""
    if not REDIS_URL or not REDIS_TOKEN:
        return None
    try:
        r = requests.get(
            f"{REDIS_URL}/get/{key}",
            headers=_headers(),
            timeout=5
        )
        if r.status_code == 200:
            result = r.json().get("result")
            if result:
                return json.loads(result)
    except Exception as e:
        logger.error(f"Redis GET {key}: {e}")
    return None


def redis_available() -> bool:
    """Проверить доступность Redis"""
    return bool(REDIS_URL and REDIS_TOKEN)
