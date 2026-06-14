import logging
import requests
from config import CRYPTOPANIC_TOKEN, NEWS_CURRENCY

logger = logging.getLogger(__name__)

BASE_URL = "https://cryptopanic.com/api/v1/posts/"


def news_available() -> bool:
    """Новости доступны только если задан токен CryptoPanic."""
    return bool(CRYPTOPANIC_TOKEN)


def fetch_news(limit: int = 8, important_only: bool = False) -> list:
    """Свежие новости по монете. Пустой список при ошибке/без токена."""
    if not CRYPTOPANIC_TOKEN:
        return []
    params = {
        "auth_token": CRYPTOPANIC_TOKEN,
        "currencies": NEWS_CURRENCY,
        "public":     "true",
    }
    if important_only:
        params["filter"] = "important"
    try:
        r    = requests.get(BASE_URL, params=params, timeout=10)
        data = r.json()
        out  = []
        for p in data.get("results", [])[:limit]:
            votes = p.get("votes", {}) or {}
            src   = p.get("source", {}) or {}
            out.append({
                "id":        p.get("id"),
                "title":     (p.get("title") or "").strip(),
                "url":       p.get("url") or src.get("domain", ""),
                "source":    src.get("title", ""),
                "published": (p.get("published_at", "") or "")[:16].replace("T", " "),
                "positive":  int(votes.get("positive", 0)),
                "negative":  int(votes.get("negative", 0)),
                "important": int(votes.get("important", 0)),
            })
        return out
    except Exception as e:
        logger.error(f"fetch_news: {e}")
        return []


def sentiment(posts: list) -> tuple[str, str]:
    """Простой сигнал настроения по голосам (без ИИ)."""
    pos = sum(p["positive"] for p in posts)
    neg = sum(p["negative"] for p in posts)
    if pos == 0 and neg == 0:
        return "⚪", "Нейтрально (мало голосов)"
    if pos > neg * 1.5:
        return "🟢", "Преобладает позитив"
    if neg > pos * 1.5:
        return "🔴", "Преобладает негатив"
    return "🟡", "Смешанные настроения"
