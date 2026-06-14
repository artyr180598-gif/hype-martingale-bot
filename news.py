import logging
import requests
from config import (
    CRYPTOCOMPARE_TOKEN, NEWS_KEYWORDS, NEWS_TICKER
)

logger = logging.getLogger(__name__)

# Бесплатный новостной API CryptoCompare (CCData). Работает без ключа.
BASE_URL = "https://min-api.cryptocompare.com/data/v2/news/"

# Простые словари настроения (без ИИ) — оценка по заголовкам.
POS_WORDS = [
    "surge", "rally", "bull", "gain", "soar", "jump", "partnership",
    "launch", "adoption", "record", "breakout", "integration",
    "listing", "upgrade", "growth", "rise", "boost", "all-time high",
]
NEG_WORDS = [
    "hack", "exploit", "crash", "dump", "bear", "plunge", "lawsuit",
    "ban", "sell-off", "scam", "outage", "delist", "fud", "drop",
    "decline", "fall", "liquidat", "down", "warning",
]


def news_available() -> bool:
    """Источник бесплатный и без ключа — всегда доступен (если есть сеть)."""
    return True


def _matches(article: dict) -> bool:
    """Точная фильтрация по HYPE: тег-тикер или 'hyperliquid'/'$hype'.
    Слово 'hype' само по себе НЕ ловим, чтобы не было ложных срабатываний."""
    cats = (
        (article.get("categories", "") or "") + "|" +
        (article.get("tags", "") or "")
    ).upper()
    tokens = [t.strip() for t in cats.split("|") if t.strip()]
    if NEWS_TICKER.upper() in tokens:
        return True
    text = (
        (article.get("title", "") or "") + " " +
        (article.get("body", "") or "")
    ).lower()
    return any(kw in text for kw in NEWS_KEYWORDS)


def fetch_news(limit: int = 8, important_only: bool = False) -> list:
    """Свежие новости по HYPE. Пустой список при ошибке/отсутствии."""
    params = {"lang": "EN"}
    if CRYPTOCOMPARE_TOKEN:
        params["api_key"] = CRYPTOCOMPARE_TOKEN
    try:
        r    = requests.get(BASE_URL, params=params, timeout=10)
        data = r.json()
        articles = data.get("Data", []) or []
        out = []
        for a in articles:
            if not _matches(a):
                continue
            ts = a.get("published_on", 0)
            from datetime import datetime, timezone
            when = (
                datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%d.%m %H:%M")
                if ts else ""
            )
            out.append({
                "id":        str(a.get("id", "")),
                "title":     (a.get("title") or "").strip(),
                "url":       a.get("url") or a.get("guid", ""),
                "source":    a.get("source_info", {}).get("name")
                             or a.get("source", ""),
                "published": when,
            })
            if len(out) >= limit:
                break
        return out
    except Exception as e:
        logger.error(f"fetch_news: {e}")
        return []


def sentiment(posts: list) -> tuple[str, str]:
    """Простой сигнал настроения по ключевым словам в заголовках."""
    pos = neg = 0
    for p in posts:
        t = p["title"].lower()
        pos += sum(1 for w in POS_WORDS if w in t)
        neg += sum(1 for w in NEG_WORDS if w in t)
    if pos == 0 and neg == 0:
        return "⚪", "Нейтрально"
    if pos > neg * 1.5:
        return "🟢", "Преобладает позитив"
    if neg > pos * 1.5:
        return "🔴", "Преобладает негатив"
    return "🟡", "Смешанные настроения"
